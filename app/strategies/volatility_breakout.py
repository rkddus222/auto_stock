from .base import Strategy
from app.api import kis_market
from app.core.config import settings
from app.core.logger import logger
import numpy as np


class VolatilityBreakout(Strategy):
    """변동성 돌파 전략 구현체 (트레일링 스톱 추가)"""

    def __init__(self, ma_period=20, trailing_stop_pct=3.0, k=None):
        self.k = k if k is not None else settings.VOLATILITY_BREAKOUT_K
        self.ma_period = ma_period
        self.trailing_stop_pct = trailing_stop_pct
        logger.debug(f"전략 초기화: 변동성 돌파(K={self.k}) + {self.ma_period}일 MA 필터 | 트레일링 스톱: {self.trailing_stop_pct}%")

    def get_strategy_name(self) -> str:
        return "volatility_breakout"

    def get_parameters(self) -> dict:
        return {
            "k": self.k,
            "ma_period": self.ma_period,
            "trailing_stop_pct": self.trailing_stop_pct,
        }

    @classmethod
    def get_param_schema(cls) -> list[dict]:
        return [
            {"name": "k", "type": "float", "default": 0.5, "description": "변동성 돌파 K값 (0~1)"},
            {"name": "ma_period", "type": "int", "default": 20, "description": "이동평균 기간 (일)"},
            {"name": "trailing_stop_pct", "type": "float", "default": 3.0, "description": "트레일링 스톱 비율 (%)"},
        ]

    def check_signal(self, symbol: str) -> tuple[str, float | None]:
        """매수 신호 확인 로직"""
        try:
            daily_data = kis_market.get_daily_ohlcv(symbol, days=self.ma_period + 2)
            if not daily_data or len(daily_data) < self.ma_period + 1:
                logger.warning(f"[{symbol}] {self.ma_period}일 MA 계산을 위한 데이터가 부족합니다.")
                return "HOLD", None

            closing_prices = [float(day['stck_clpr']) for day in daily_data[1:self.ma_period + 1]]
            ma20 = np.mean(closing_prices)
            current_price = kis_market.get_current_price(symbol)

            indicators = {"ma": round(ma20, 2), "current_price": current_price, "k": self.k}

            if current_price < ma20:
                logger.debug(f"[{symbol}] 하락 추세, 매수 보류 (현재가: {current_price} < MA: {ma20})")
                self.log_decision(symbol, "HOLD", f"하락 추세 (현재가 < MA{self.ma_period})",
                                  indicators, current_price, "SKIPPED")
                return "HOLD", None

            logger.debug(f"[{symbol}] 상승 추세 확인 (현재가: {current_price} > MA: {ma20})")

            prev_day = daily_data[1]
            prev_high = float(prev_day["stck_hgpr"])
            prev_low = float(prev_day["stck_lwpr"])
            today_open = float(daily_data[0]["stck_oprc"])

            volatility = prev_high - prev_low
            target_price = today_open + volatility * self.k
            indicators["target_price"] = round(target_price, 2)
            indicators["volatility"] = round(volatility, 2)

            if current_price >= target_price:
                logger.debug(f"[{symbol}] 매수 신호 발생! 목표가 {target_price:.2f} 돌파")
                self.log_decision(symbol, "BUY", f"변동성 돌파 (현재가 >= 목표가 {target_price:.0f})",
                                  indicators, current_price, "EXECUTED")
                return "BUY", current_price

            self.log_decision(symbol, "HOLD", f"목표가 미도달 (현재가 < {target_price:.0f})",
                              indicators, current_price, "SKIPPED")

        except Exception as e:
            logger.error(f"[{symbol}] 신호 확인 중 에러 발생: {e}")

        return "HOLD", None
