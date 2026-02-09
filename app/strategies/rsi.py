"""RSI 전략: RSI < 30 매수, RSI > 70 매도"""

import numpy as np

from app.api import kis_market
from app.core.logger import logger
from app.strategies.base import Strategy


def _compute_rsi(prices: list[float], period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class RSIStrategy(Strategy):
    """RSI 과매도(< 30) 시 매수, 과매수(> 70) 시 매도"""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0, trailing_stop_pct: float = 5.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.trailing_stop_pct = trailing_stop_pct

    def get_strategy_name(self) -> str:
        return "rsi"

    def get_parameters(self) -> dict:
        return {
            "period": self.period,
            "oversold": self.oversold,
            "overbought": self.overbought,
            "trailing_stop_pct": self.trailing_stop_pct,
        }

    @classmethod
    def get_param_schema(cls) -> list[dict]:
        return [
            {"name": "period", "type": "int", "default": 14, "description": "RSI 기간"},
            {"name": "oversold", "type": "float", "default": 30.0, "description": "과매도 기준 (이하 매수)"},
            {"name": "overbought", "type": "float", "default": 70.0, "description": "과매수 기준 (이상 매도)"},
            {"name": "trailing_stop_pct", "type": "float", "default": 5.0, "description": "트레일링 스톱 비율 (%)"},
        ]

    def check_signal(self, symbol: str) -> tuple[str, float | None]:
        try:
            days = self.period + 20
            daily_data = kis_market.get_daily_ohlcv(symbol, days=days)
            if not daily_data or len(daily_data) < days:
                logger.warning(f"[{symbol}] RSI 계산을 위한 데이터가 부족합니다.")
                return "HOLD", None

            closes = [float(d["stck_clpr"]) for d in daily_data[1:days]]
            closes_asc = list(reversed(closes))
            rsi_val = _compute_rsi(closes_asc, self.period)
            current_price = kis_market.get_current_price(symbol)
            indicators = {"rsi": round(rsi_val, 2), "current_price": current_price}

            if rsi_val < self.oversold:
                logger.info(f"[{symbol}] RSI 과매도 매수 신호 (RSI={rsi_val:.1f})")
                self.log_decision(
                    symbol, "BUY", f"RSI 과매도 (RSI={rsi_val:.1f} < {self.oversold})", indicators, current_price, "EXECUTED"
                )
                return "BUY", current_price
            if rsi_val > self.overbought:
                logger.info(f"[{symbol}] RSI 과매수 매도 신호 (RSI={rsi_val:.1f})")
                self.log_decision(
                    symbol, "SELL", f"RSI 과매수 (RSI={rsi_val:.1f} > {self.overbought})", indicators, current_price, "EXECUTED"
                )
                return "SELL", None

            self.log_decision(
                symbol, "HOLD", f"RSI 중립 (RSI={rsi_val:.1f})", indicators, current_price, "SKIPPED"
            )
        except Exception as e:
            logger.error(f"[{symbol}] RSI 신호 확인 중 에러: {e}")
        return "HOLD", None


from app.strategies.registry import StrategyRegistry
StrategyRegistry.register("rsi", RSIStrategy)
