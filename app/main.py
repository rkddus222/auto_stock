import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

# app/ 에서 python main.py 로 실행해도 app 패키지를 찾을 수 있도록 루트를 path에 추가
_MAIN_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_MAIN_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

try:
    import asyncio
    from fastapi import FastAPI, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ModuleNotFoundError as e:
    print("필요한 패키지가 없습니다. 프로젝트 루트(auto_stock)에서 아래를 실행하세요:", file=sys.stderr)
    print("  pip install -r requirements.txt", file=sys.stderr)
    print("실행은 루트에서: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000", file=sys.stderr)
    sys.exit(1)

from app.api import kis_order, kis_market
from app.core.config import settings
from app.core.logger import logger
from app.core.slack import send_slack_notification
from app.db import models, session
from app.db.models import OrderType, OrderStatus
from app.services import portfolio as portfolio_service
from app.services import reconciliation as reconciliation_service
from app.strategies.registry import StrategyRegistry
from app.services.websocket_manager import ws_manager

# --- Global Variables & Settings ---
TRADE_STATUS_FILE: Path = settings.base_dir / "trade_status.json"
scheduler = AsyncIOScheduler()
target_symbols: list[str] = []
trade_status: dict = {}
trading_enabled = True  # API로 on/off 가능

# WebSocket: 매매 이벤트 브로드캐스트용 (스레드에서 넣고, async 태스크가 전송)
_pending_broadcasts: list[dict] = []
_broadcast_lock = threading.Lock()

DEFAULT_STRATEGY_NAME = "volatility_breakout"
DEFAULT_STRATEGY_PARAMS = {"ma_period": 20, "trailing_stop_pct": 5.0, "k": getattr(settings, "VOLATILITY_BREAKOUT_K", 0.5)}


def get_strategy_for_symbol(symbol: str):
    """종목별 StrategyConfig를 조회해 전략 인스턴스를 반환합니다. 없으면 기본 변동성 돌파."""
    db = session.SessionLocal()
    try:
        row = (
            db.query(models.StrategyConfig)
            .filter(models.StrategyConfig.symbol == symbol, models.StrategyConfig.is_active == True)
            .order_by(models.StrategyConfig.updated_at.desc())
            .first()
        )
        if row and row.strategy_name:
            params = json.loads(row.parameters) if isinstance(row.parameters, str) else (row.parameters or {})
            return StrategyRegistry.get_strategy(row.strategy_name, params)
    except Exception as e:
        logger.debug(f"종목 {symbol} 전략 설정 조회 실패, 기본 전략 사용: {e}")
    finally:
        db.close()
    return StrategyRegistry.get_strategy(DEFAULT_STRATEGY_NAME, DEFAULT_STRATEGY_PARAMS)

# --- State Persistence Functions ---
def save_trade_status():
    """거래 상태를 JSON 파일에 저장합니다."""
    TRADE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(trade_status, f, indent=4, ensure_ascii=False)


def load_trade_status():
    """JSON 파일에서 거래 상태를 불러옵니다."""
    global trade_status, target_symbols
    target_symbols = settings.target_symbols_list
    if not target_symbols:
        logger.warning("TARGET_SYMBOLS가 비어 있습니다. 기본 종목을 사용합니다.")
        target_symbols = ["005930", "000660"]
    try:
        with open(TRADE_STATUS_FILE, "r", encoding="utf-8") as f:
            trade_status = json.load(f)
        logger.info("거래 상태를 파일에서 성공적으로 불러왔습니다.")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("거래 상태 파일을 찾을 수 없어 새로 생성합니다.")
        trade_status = {
            symbol: {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
            for symbol in target_symbols
        }

def _log_trade(symbol: str, order_type: str, price: float, quantity: int, status: OrderStatus, kis_response: dict | None):
    """주문 결과를 DB에 기록합니다."""
    try:
        db = session.SessionLocal()
        try:
            log = models.TradeLog(
                symbol=symbol,
                order_type=OrderType.BUY if order_type == "BUY" else OrderType.SELL,
                price=price,
                quantity=quantity,
                status=status,
                kis_response=json.dumps(kis_response, ensure_ascii=False) if kis_response else None,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"거래 로그 저장 실패: {e}")


# --- Trading Jobs ---
def run_trading_strategy():
    """자동 매매 전략 실행 (매수 및 트레일링 스톱)"""
    if not trading_enabled:
        return
    logger.info("자동 매매 로직 실행: 매수 및 트레일링 스톱 모니터링")
    try:
        cash_balance = kis_order.get_cash_balance()
        ratio = max(0.01, min(1.0, settings.BUDGET_RATIO))
        budget_per_stock = (cash_balance * ratio) / len(target_symbols) if target_symbols else 0
    except Exception as e:
        logger.error(f"예수금 조회 실패: {e}")
        return

    for symbol in target_symbols:
        try:
            strategy = get_strategy_for_symbol(symbol)
            trailing_pct = strategy.get_parameters().get("trailing_stop_pct", 5.0)
            current_price = kis_market.get_current_price(symbol)

            # 1. 매수 상태: 트레일링 스톱 및 전략 SELL 신호 확인
            if trade_status.get(symbol, {}).get("bought"):
                stop_price = trade_status[symbol]["stop_price"]
                quantity = trade_status[symbol]["quantity"]

                # 전략 SELL 신호 확인
                signal, _ = strategy.check_signal(symbol)
                if signal == "SELL":
                    logger.info(f"[{symbol}] 전략 매도 신호 실행")
                    try:
                        res = kis_order.place_order(symbol=symbol, quantity=quantity, price=0, order_type="SELL")
                        _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.EXECUTED, res)
                        send_slack_notification(f"[전략 매도] {symbol} ({quantity}주) | 현재가: {current_price:.0f}")
                        trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
                        save_trade_status()
                        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": quantity})
                    except Exception as e:
                        _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.FAILED, None)
                        logger.error(f"[{symbol}] 매도 주문 실패: {e}")
                    time.sleep(0.2)
                    continue

                # 손절 조건 확인 (트레일링 스톱)
                if current_price <= stop_price:
                    logger.info(f"[{symbol}] 트레일링 스톱 매도! 현재가: {current_price}, 손절가: {stop_price}")
                    try:
                        res = kis_order.place_order(symbol=symbol, quantity=quantity, price=0, order_type="SELL")
                        _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.EXECUTED, res)
                        send_slack_notification(f"[트레일링 스톱] {symbol} ({quantity}주) | 현재가: {current_price:.0f}")
                        trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
                        save_trade_status()
                        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": current_price, "quantity": quantity})
                    except Exception as e:
                        _log_trade(symbol, "SELL", current_price, quantity, OrderStatus.FAILED, None)
                        logger.error(f"[{symbol}] 매도 주문 실패: {e}")
                    continue

                # 트레일링 스톱 가격 상향 조정
                new_stop_price = current_price * (1 - trailing_pct / 100)
                if new_stop_price > stop_price:
                    trade_status[symbol]["stop_price"] = new_stop_price
                    logger.info(f"[{symbol}] 스톱 가격 상향 조정 -> {new_stop_price:.0f}")
                    save_trade_status()

            # 2. 미매수 상태: 매수 신호 확인
            else:
                signal, price_at_signal = strategy.check_signal(symbol)
                if signal == "BUY" and price_at_signal is not None:
                    quantity_to_buy = int(budget_per_stock // price_at_signal)
                    if quantity_to_buy < 1:
                        time.sleep(0.2)
                        continue

                    try:
                        res = kis_order.place_order(symbol=symbol, quantity=quantity_to_buy, price=0, order_type="BUY")
                        _log_trade(symbol, "BUY", price_at_signal, quantity_to_buy, OrderStatus.EXECUTED, res)
                        initial_stop_price = price_at_signal * (1 - trailing_pct / 100)
                        trade_status[symbol] = {
                            "bought": True,
                            "purchase_price": price_at_signal,
                            "quantity": quantity_to_buy,
                            "stop_price": initial_stop_price,
                        }
                        logger.info(f"[{symbol}] 매수 성공! 손절가: {initial_stop_price:.0f}")
                        send_slack_notification(f"[매수 성공] {symbol}({quantity_to_buy}주) | 매수가: {price_at_signal:.0f}")
                        save_trade_status()
                        queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "BUY", "price": price_at_signal, "quantity": quantity_to_buy})
                    except Exception as e:
                        _log_trade(symbol, "BUY", price_at_signal, quantity_to_buy, OrderStatus.FAILED, None)
                        logger.error(f"[{symbol}] 매수 주문 실패: {e}")

            time.sleep(0.2)

        except Exception as e:
            logger.error(f"[{symbol}] 매매 로직 실행 중 에러: {e}")


def sell_all_at_close():
    """장 마감 전량 매도 (스케줄 또는 API 호출)"""
    logger.info("장 마감 전량 매도 로직을 시작합니다.")
    for symbol in target_symbols:
        if trade_status.get(symbol, {}).get("bought"):
            quantity = trade_status[symbol]["quantity"]
            try:
                res = kis_order.place_order(symbol=symbol, quantity=quantity, price=0, order_type="SELL")
                _log_trade(symbol, "SELL", 0.0, quantity, OrderStatus.EXECUTED, res)
                send_slack_notification(f"[장 마감 매도] {symbol}, 수량: {quantity}")
                trade_status[symbol] = {"bought": False, "purchase_price": 0.0, "quantity": 0, "stop_price": 0.0}
                queue_broadcast({"type": "trade_event", "symbol": symbol, "side": "SELL", "price": 0.0, "quantity": quantity})
            except Exception as e:
                _log_trade(symbol, "SELL", 0.0, quantity, OrderStatus.FAILED, None)
                logger.error(f"[{symbol}] 장 마감 매도 실패: {e}")
            time.sleep(1)
    save_trade_status()


def job_portfolio_snapshot():
    """5분마다 포트폴리오 스냅샷 저장"""
    try:
        portfolio_service.create_portfolio_snapshot(trade_status)
    except Exception as e:
        logger.error(f"포트폴리오 스냅샷 실패: {e}")


def job_reconciliation():
    """30분마다 포지션 정합성 검사"""
    try:
        reconciliation_service.run_reconciliation(trade_status)
    except Exception as e:
        logger.error(f"리콘실리에이션 실패: {e}")


def queue_broadcast(message: dict) -> None:
    """스레드(스케줄러)에서 호출: WebSocket 브로드캐스트를 대기열에 넣습니다."""
    with _broadcast_lock:
        _pending_broadcasts.append(message)


async def price_update_broadcaster():
    """5초마다 상태 브로드캐스트 및 대기 중인 trade_event 전송."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(5)
        try:
            status = await loop.run_in_executor(None, get_status)
            await ws_manager.broadcast({"type": "status_update", "payload": status})
        except Exception as e:
            logger.debug(f"status broadcast 오류: {e}")
        with _broadcast_lock:
            pending = _pending_broadcasts[:]
            _pending_broadcasts.clear()
        for msg in pending:
            try:
                await ws_manager.broadcast(msg)
            except Exception as e:
                logger.debug(f"trade_event broadcast 오류: {e}")


# --- FastAPI Lifespan & App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=session.engine)
    load_trade_status()
    scheduler.add_job(run_trading_strategy, 'cron', day_of_week='mon-fri', hour='9-15', minute='*', second='*/10', id="trading_job")
    scheduler.add_job(sell_all_at_close, 'cron', day_of_week='mon-fri', hour=15, minute=19, id="sell_all_job")
    scheduler.add_job(job_portfolio_snapshot, 'cron', minute='*/5', id="portfolio_snapshot_job")
    scheduler.add_job(job_reconciliation, 'cron', minute='0,30', id="reconciliation_job")
    scheduler.start()
    asyncio.create_task(price_update_broadcaster())
    logger.info("고도화된 자동매매 시스템이 시작되었습니다.")
    send_slack_notification("고도화된 자동매매 시스템이 시작되었습니다.")
    yield
    scheduler.shutdown()
    logger.info("자동매매 시스템이 종료되었습니다.")

app = FastAPI(lifespan=lifespan)

# CORS: 프론트엔드(localhost:5173 등)에서 API 호출 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # 접속 시 초기 상태 전송
        initial = get_status()
        await websocket.send_json({"type": "status_update", "payload": initial})
        # ping/pong 하트비트 및 수신 대기
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                await websocket.send_text("pong")
    except Exception:
        pass
    finally:
        ws_manager.disconnect(websocket)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Advanced Auto Trading System is running."}


@app.get("/api/status")
def get_status():
    """총자산, 예수금, 보유종목, 봇 on/off 상태 반환"""
    assets_error = None
    try:
        cash = kis_order.get_cash_balance()
    except Exception as e:
        logger.error(f"예수금 조회 실패: {e}")
        cash = 0
        assets_error = str(e)
    total_holding = 0.0
    for symbol, st in trade_status.items():
        if st.get("bought"):
            try:
                price = kis_market.get_current_price(symbol)
                total_holding += price * st["quantity"]
            except Exception:
                total_holding += st["purchase_price"] * st["quantity"]
    # 당일 실현손익: 오늘 체결된 매도 금액 (단순화)
    from datetime import datetime
    today_pl = 0.0
    db = session.SessionLocal()
    try:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            db.query(models.TradeLog)
            .filter(
                models.TradeLog.timestamp >= today_start,
                models.TradeLog.order_type == OrderType.SELL,
                models.TradeLog.status == OrderStatus.EXECUTED,
            )
            .all()
        )
        today_pl = sum(r.price * r.quantity for r in rows if r.price and r.quantity)
    except Exception as e:
        logger.debug(f"당일 손익 집계 스킵: {e}")
    finally:
        db.close()
    total_assets = cash + total_holding
    return_rate = (today_pl / total_assets * 100) if total_assets else 0.0
    positions_detail = portfolio_service.calculate_unrealized_pl(trade_status)
    return {
        "totalAssets": total_assets,
        "cashBalance": cash,
        "todayRealizedPL": today_pl,
        "returnRate": round(return_rate, 2),
        "positions": trade_status,
        "positionsDetail": positions_detail,
        "tradingEnabled": trading_enabled,
        "targetSymbols": target_symbols,
        "assetsError": assets_error,
    }


@app.get("/api/decisions")
def get_decisions(symbol: str = "", limit: int = 50):
    """의사결정 로그를 조회합니다."""
    db = session.SessionLocal()
    try:
        query = db.query(models.DecisionLog).order_by(models.DecisionLog.timestamp.desc()).limit(limit)
        if symbol:
            query = query.filter(models.DecisionLog.symbol == symbol)
        rows = query.all()
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "symbol": r.symbol,
                "strategy_name": r.strategy_name,
                "signal": r.signal,
                "decision_reason": r.decision_reason,
                "indicator_values": json.loads(r.indicator_values) if r.indicator_values else {},
                "current_price": r.current_price,
                "action_taken": r.action_taken,
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/api/trades")
def get_trades(limit: int = 50):
    """최근 매매 로그 (DB)"""
    db = session.SessionLocal()
    try:
        logs = (
            db.query(models.TradeLog)
            .order_by(models.TradeLog.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": l.id,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                "symbol": l.symbol,
                "orderType": l.order_type.value if l.order_type else None,
                "price": l.price,
                "quantity": l.quantity,
                "status": l.status.value if l.status else None,
            }
            for l in logs
        ]
    finally:
        db.close()


@app.post("/api/bot/start")
def bot_start():
    """자동매매 봇 활성화"""
    global trading_enabled
    trading_enabled = True
    return {"success": True, "tradingEnabled": True}


@app.post("/api/bot/stop")
def bot_stop():
    """자동매매 봇 일시 정지"""
    global trading_enabled
    trading_enabled = False
    return {"success": True, "tradingEnabled": False}


@app.post("/api/panic-sell")
def api_panic_sell():
    """전량 매도 (Panic Sell)"""
    sell_all_at_close()
    return {"success": True, "message": "전량 매도 주문을 실행했습니다."}


# 라우터 등록 (전략/포트폴리오 API)
from app.routers import strategies as strategies_router
from app.routers import portfolio as portfolio_router
app.include_router(strategies_router.router, prefix="/api")
app.include_router(portfolio_router.router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
