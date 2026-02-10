"""
스키마 마이그레이션: 기존 DB에 새 컬럼 추가 등
"""
from sqlalchemy import text

from .session import engine
from . import models
from ..core.logger import logger


def run_migrations():
    """누락된 컬럼 등을 추가합니다."""
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='trade_logs'"))
            if result.fetchone() is None:
                return  # 테이블이 없으면 create_all이 새로 만들 것이므로 스킵
            result = conn.execute(text("PRAGMA table_info(trade_logs)"))
            columns = [row[1] for row in result]
            if "realized_pl" not in columns:
                conn.execute(text("ALTER TABLE trade_logs ADD COLUMN realized_pl REAL DEFAULT 0.0"))
                conn.commit()
                logger.info("trade_logs.realized_pl 컬럼을 추가했습니다.")
        except Exception as e:
            logger.warning(f"마이그레이션 실패: {e}")
            conn.rollback()
