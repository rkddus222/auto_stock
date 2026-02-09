import json
from datetime import datetime, timedelta

from app.api import kis_order, kis_market
from app.core.logger import logger
from app.db import models, session
from app.db.models import OrderType, OrderStatus


def calculate_unrealized_pl(trade_status: dict) -> list[dict]:
    """보유종목별 평가손익을 계산합니다."""
    results = []
    for symbol, st in trade_status.items():
        if not st.get("bought"):
            continue
        try:
            current_price = kis_market.get_current_price(symbol)
            purchase_price = st["purchase_price"]
            quantity = st["quantity"]
            unrealized = (current_price - purchase_price) * quantity
            unrealized_pct = ((current_price - purchase_price) / purchase_price * 100) if purchase_price else 0.0
            results.append({
                "symbol": symbol,
                "quantity": quantity,
                "purchasePrice": purchase_price,
                "currentPrice": current_price,
                "unrealizedPL": round(unrealized, 2),
                "unrealizedPLPct": round(unrealized_pct, 2),
                "stopPrice": st.get("stop_price", 0.0),
            })
        except Exception as e:
            logger.error(f"[{symbol}] 평가손익 계산 실패: {e}")
            results.append({
                "symbol": symbol,
                "quantity": st["quantity"],
                "purchasePrice": st["purchase_price"],
                "currentPrice": st["purchase_price"],
                "unrealizedPL": 0.0,
                "unrealizedPLPct": 0.0,
                "stopPrice": st.get("stop_price", 0.0),
            })
    return results


def calculate_realized_pl(start_date: datetime | None = None) -> float:
    """실현손익을 집계합니다."""
    db = session.SessionLocal()
    try:
        query = db.query(models.TradeLog).filter(
            models.TradeLog.order_type == OrderType.SELL,
            models.TradeLog.status == OrderStatus.EXECUTED,
        )
        if start_date:
            query = query.filter(models.TradeLog.timestamp >= start_date)
        rows = query.all()
        return sum(r.realized_pl or 0.0 for r in rows)
    except Exception as e:
        logger.error(f"실현손익 집계 실패: {e}")
        return 0.0
    finally:
        db.close()


def create_portfolio_snapshot(trade_status: dict):
    """포트폴리오 스냅샷을 저장합니다."""
    try:
        cash = kis_order.get_cash_balance()
    except Exception as e:
        logger.error(f"포트폴리오 스냅샷 - 예수금 조회 실패: {e}")
        return

    holdings_value = 0.0
    total_unrealized = 0.0
    positions = calculate_unrealized_pl(trade_status)
    for pos in positions:
        holdings_value += pos["currentPrice"] * pos["quantity"]
        total_unrealized += pos["unrealizedPL"]

    total_assets = cash + holdings_value
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    realized = calculate_realized_pl(today_start)

    # 일일 수익률: 전일 마지막 스냅샷 대비
    daily_return_pct = 0.0
    db = session.SessionLocal()
    try:
        yesterday = today_start - timedelta(days=1)
        prev_snapshot = (
            db.query(models.PortfolioSnapshot)
            .filter(models.PortfolioSnapshot.timestamp < today_start)
            .order_by(models.PortfolioSnapshot.timestamp.desc())
            .first()
        )
        if prev_snapshot and prev_snapshot.total_assets > 0:
            daily_return_pct = (total_assets - prev_snapshot.total_assets) / prev_snapshot.total_assets * 100

        snapshot = models.PortfolioSnapshot(
            total_assets=total_assets,
            cash_balance=cash,
            holdings_value=holdings_value,
            realized_pl=realized,
            unrealized_pl=total_unrealized,
            daily_return_pct=round(daily_return_pct, 4),
        )
        db.add(snapshot)
        db.commit()
        logger.info(f"포트폴리오 스냅샷 저장: 총자산={total_assets:.0f}, 현금={cash:.0f}, 보유={holdings_value:.0f}")
    except Exception as e:
        logger.error(f"포트폴리오 스냅샷 저장 실패: {e}")
    finally:
        db.close()
