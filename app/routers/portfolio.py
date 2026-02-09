"""포트폴리오 히스토리 및 성과 API"""

from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from app.db import models, session

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/history")
def get_portfolio_history(days: int = Query(7, ge=1, le=90)):
    """지정 일수만큼의 포트폴리오 스냅샷 시계열을 반환합니다 (차트용)."""
    db = session.SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            db.query(models.PortfolioSnapshot)
            .filter(models.PortfolioSnapshot.timestamp >= since)
            .order_by(models.PortfolioSnapshot.timestamp.asc())
            .all()
        )
        return [
            {
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "totalAssets": r.total_assets,
                "cashBalance": r.cash_balance,
                "holdingsValue": r.holdings_value,
                "realizedPL": r.realized_pl,
                "unrealizedPL": r.unrealized_pl,
                "dailyReturnPct": r.daily_return_pct,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/performance")
def get_portfolio_performance():
    """전체 수익률 메트릭 (가장 최근 스냅샷 기준)."""
    db = session.SessionLocal()
    try:
        row = (
            db.query(models.PortfolioSnapshot)
            .order_by(models.PortfolioSnapshot.timestamp.desc())
            .first()
        )
        if not row:
            return {
                "totalAssets": 0.0,
                "cashBalance": 0.0,
                "holdingsValue": 0.0,
                "realizedPL": 0.0,
                "unrealizedPL": 0.0,
                "dailyReturnPct": 0.0,
            }
        return {
            "totalAssets": row.total_assets,
            "cashBalance": row.cash_balance,
            "holdingsValue": row.holdings_value,
            "realizedPL": row.realized_pl,
            "unrealizedPL": row.unrealized_pl,
            "dailyReturnPct": row.daily_return_pct,
        }
    finally:
        db.close()
