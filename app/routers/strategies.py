"""전략 목록 및 종목별 전략 설정 API"""

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import models, session
from app.strategies.registry import StrategyRegistry

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/list")
def list_strategies():
    """사용 가능한 전략 목록과 파라미터 스키마를 반환합니다."""
    return {"strategies": StrategyRegistry.list_strategies()}


@router.get("/config/{symbol}")
def get_strategy_config(symbol: str):
    """종목별 전략 설정을 조회합니다."""
    db = session.SessionLocal()
    try:
        row = (
            db.query(models.StrategyConfig)
            .filter(models.StrategyConfig.symbol == symbol, models.StrategyConfig.is_active == True)
            .order_by(models.StrategyConfig.updated_at.desc())
            .first()
        )
        if not row:
            return {"symbol": symbol, "strategy_name": None, "parameters": {}}
        params = json.loads(row.parameters) if isinstance(row.parameters, str) else (row.parameters or {})
        return {
            "symbol": symbol,
            "strategy_name": row.strategy_name,
            "parameters": params,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    finally:
        db.close()


class StrategyConfigBody(BaseModel):
    symbol: str
    strategy_name: str
    parameters: dict = {}


@router.post("/config")
def save_strategy_config(body: StrategyConfigBody):
    """종목별 전략 설정을 저장합니다. 기존 설정이 있으면 업데이트합니다."""
    db = session.SessionLocal()
    try:
        row = (
            db.query(models.StrategyConfig)
            .filter(models.StrategyConfig.symbol == body.symbol)
            .order_by(models.StrategyConfig.updated_at.desc())
            .first()
        )
        params_str = json.dumps(body.parameters, ensure_ascii=False)
        if row:
            row.strategy_name = body.strategy_name
            row.parameters = params_str
            row.is_active = True
            db.commit()
            return {"success": True, "symbol": body.symbol, "strategy_name": body.strategy_name}
        new_row = models.StrategyConfig(
            symbol=body.symbol,
            strategy_name=body.strategy_name,
            parameters=params_str,
            is_active=True,
        )
        db.add(new_row)
        db.commit()
        return {"success": True, "symbol": body.symbol, "strategy_name": body.strategy_name}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
