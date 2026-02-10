"""볼린저 밴드 전략: 하단 밴드 매수, 상단 밴드 매도"""

import numpy as np

from app.api import kis_market
from app.core.logger import logger
from app.strategies.base import Strategy


class BollingerStrategy(Strategy):
    """가격이 하단 밴드 터치 시 매수, 상단 밴드 터치 시 매도"""

    def __init__(self, period: int = 20, std_dev: float = 2.0, trailing_stop_pct: float = 5.0):
        self.period = period
        self.std_dev = std_dev
        self.trailing_stop_pct = trailing_stop_pct

    def get_strategy_name(self) -> str:
        return "bollinger"

    def get_parameters(self) -> dict:
        return {
            "period": self.period,
            "std_dev": self.std_dev,
            "trailing_stop_pct": self.trailing_stop_pct,
        }

    @classmethod
    def get_param_schema(cls) -> list[dict]:
        return [
            {"name": "period", "type": "int", "default": 20, "description": "이동평균/표준편차 기간"},
            {"name": "std_dev", "type": "float", "default": 2.0, "description": "밴드 표준편차 배수"},
            {"name": "trailing_stop_pct", "type": "float", "default": 5.0, "description": "트레일링 스톱 비율 (%)"},
        ]

    def check_signal(self, symbol: str) -> tuple[str, float | None]:
        try:
            days = self.period + 5
            daily_data = kis_market.get_daily_ohlcv(symbol, days=days)
            if not daily_data or len(daily_data) < days:
                logger.warning(f"[{symbol}] 볼린저 계산을 위한 데이터가 부족합니다.")
                return "HOLD", None

            closes = np.array([float(d["stck_clpr"]) for d in daily_data[1:days]])
            ma = np.mean(closes[-self.period :])
            std = np.std(closes[-self.period :])
            if std == 0:
                std = 1e-10
            upper = ma + self.std_dev * std
            lower = ma - self.std_dev * std
            current_price = kis_market.get_current_price(symbol)
            indicators = {
                "upper": round(upper, 2),
                "lower": round(lower, 2),
                "ma": round(ma, 2),
                "current_price": current_price,
            }

            if current_price <= lower:
                logger.debug(f"[{symbol}] 볼린저 하단 밴드 매수 신호 (현재가={current_price:.0f}, 하단={lower:.0f})")
                self.log_decision(
                    symbol, "BUY", "가격이 하단 밴드 이하", indicators, current_price, "EXECUTED"
                )
                return "BUY", current_price
            if current_price >= upper:
                logger.debug(f"[{symbol}] 볼린저 상단 밴드 매도 신호 (현재가={current_price:.0f}, 상단={upper:.0f})")
                self.log_decision(
                    symbol, "SELL", "가격이 상단 밴드 이상", indicators, current_price, "EXECUTED"
                )
                return "SELL", None

            self.log_decision(
                symbol, "HOLD", f"밴드 내 (상={upper:.0f}, 하={lower:.0f})", indicators, current_price, "SKIPPED"
            )
        except Exception as e:
            logger.error(f"[{symbol}] 볼린저 신호 확인 중 에러: {e}")
        return "HOLD", None


from app.strategies.registry import StrategyRegistry
StrategyRegistry.register("bollinger", BollingerStrategy)
