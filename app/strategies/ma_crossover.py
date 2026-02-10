"""이동평균 교차 전략: 단기 MA 상향 돌파 시 매수, 하향 돌파 시 매도"""

import numpy as np

from app.api import kis_market
from app.core.logger import logger
from app.strategies.base import Strategy


class MACrossover(Strategy):
    """단기 MA가 장기 MA 상향 돌파 시 매수, 하향 돌파 시 매도"""

    def __init__(self, short_period: int = 5, long_period: int = 20, trailing_stop_pct: float = 5.0):
        self.short_period = short_period
        self.long_period = long_period
        self.trailing_stop_pct = trailing_stop_pct

    def get_strategy_name(self) -> str:
        return "ma_crossover"

    def get_parameters(self) -> dict:
        return {
            "short_period": self.short_period,
            "long_period": self.long_period,
            "trailing_stop_pct": self.trailing_stop_pct,
        }

    @classmethod
    def get_param_schema(cls) -> list[dict]:
        return [
            {"name": "short_period", "type": "int", "default": 5, "description": "단기 이동평균 기간 (일)"},
            {"name": "long_period", "type": "int", "default": 20, "description": "장기 이동평균 기간 (일)"},
            {"name": "trailing_stop_pct", "type": "float", "default": 5.0, "description": "트레일링 스톱 비율 (%)"},
        ]

    def check_signal(self, symbol: str) -> tuple[str, float | None]:
        try:
            days = self.long_period + 3
            daily_data = kis_market.get_daily_ohlcv(symbol, days=days)
            if not daily_data or len(daily_data) < days:
                logger.warning(f"[{symbol}] MA 교차 계산을 위한 데이터가 부족합니다.")
                return "HOLD", None

            closes = [float(d["stck_clpr"]) for d in daily_data[1 : days]]
            short_ma = np.mean(closes[: self.short_period])
            long_ma = np.mean(closes[: self.long_period])
            # 이전 봉 기준
            prev_short = np.mean(closes[1 : self.short_period + 1])
            prev_long = np.mean(closes[1 : self.long_period + 1])

            current_price = kis_market.get_current_price(symbol)
            indicators = {
                "short_ma": round(short_ma, 2),
                "long_ma": round(long_ma, 2),
                "current_price": current_price,
            }

            # 상향 돌파: 이전에는 short < long, 현재 short >= long
            if prev_short < prev_long and short_ma >= long_ma:
                logger.debug(f"[{symbol}] MA 상향 돌파 매수 신호 (단기={short_ma:.2f}, 장기={long_ma:.2f})")
                self.log_decision(
                    symbol, "BUY", "단기 MA가 장기 MA 상향 돌파", indicators, current_price, "EXECUTED"
                )
                return "BUY", current_price

            # 하향 돌파: 이전에는 short > long, 현재 short <= long
            if prev_short > prev_long and short_ma <= long_ma:
                logger.debug(f"[{symbol}] MA 하향 돌파 매도 신호 (단기={short_ma:.2f}, 장기={long_ma:.2f})")
                self.log_decision(
                    symbol, "SELL", "단기 MA가 장기 MA 하향 돌파", indicators, current_price, "EXECUTED"
                )
                return "SELL", None

            self.log_decision(
                symbol, "HOLD", f"MA 교차 대기 (단기={short_ma:.0f}, 장기={long_ma:.0f})", indicators, current_price, "SKIPPED"
            )
        except Exception as e:
            logger.error(f"[{symbol}] MA 교차 신호 확인 중 에러: {e}")
        return "HOLD", None


from app.strategies.registry import StrategyRegistry
StrategyRegistry.register("ma_crossover", MACrossover)
