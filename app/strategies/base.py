
import json
from abc import ABC, abstractmethod
from datetime import datetime

from app.core.logger import logger


class Strategy(ABC):
    """모든 매매 전략의 기본이 되는 추상 클래스"""

    @abstractmethod
    def check_signal(self, symbol: str) -> tuple[str, float | None]:
        """
        매수/매도/홀드 신호를 결정합니다.
        :param symbol: 주식 종목 코드
        :return: ("BUY", 목표가) or ("SELL", None) or ("HOLD", None)
        """
        pass

    @abstractmethod
    def get_strategy_name(self) -> str:
        """전략의 고유 이름을 반환합니다."""
        pass

    @abstractmethod
    def get_parameters(self) -> dict:
        """현재 전략 파라미터를 반환합니다."""
        pass

    @classmethod
    @abstractmethod
    def get_param_schema(cls) -> list[dict]:
        """
        전략 파라미터 스키마를 반환합니다.
        각 항목: {"name": str, "type": "int"|"float", "default": val, "description": str}
        """
        pass

    def log_decision(self, symbol: str, signal: str, reason: str,
                     indicator_values: dict, current_price: float, action_taken: str):
        """의사결정을 DB에 기록합니다."""
        try:
            from app.db import models, session
            db = session.SessionLocal()
            try:
                log = models.DecisionLog(
                    symbol=symbol,
                    strategy_name=self.get_strategy_name(),
                    signal=signal,
                    decision_reason=reason,
                    indicator_values=json.dumps(indicator_values, ensure_ascii=False),
                    current_price=current_price,
                    action_taken=action_taken,
                )
                db.add(log)
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"의사결정 로그 저장 실패: {e}")
