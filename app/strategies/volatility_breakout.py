from .base import Strategy
from app.api import kis_market
from app.core.config import settings
from app.core.logger import logger
import numpy as np


class VolatilityBreakout(Strategy):
    """변동성 돌파 전략 구현체 (트레일링 스톱 추가)"""

    def __init__(self, ma_period=20, trailing_stop_pct=3.0, k=None, use_adaptive_k=None):
        self.k = k if k is not None else settings.VOLATILITY_BREAKOUT_K
        self.ma_period = ma_period
        self.trailing_stop_pct = trailing_stop_pct
        self.use_adaptive_k = use_adaptive_k if use_adaptive_k is not None else getattr(settings, "USE_ADAPTIVE_K", True)
        logger.debug(f"전략 초기화: 변동성 돌파(K={self.k}, 적응형={self.use_adaptive_k}) + {self.ma_period}일 MA 필터 | 트레일링 스톱: {self.trailing_stop_pct}%")

    def get_strategy_name(self) -> str:
        return "volatility_breakout"

    def get_parameters(self) -> dict:
        return {
            "k": self.k,
            "ma_period": self.ma_period,
            "trailing_stop_pct": self.trailing_stop_pct,
            "use_adaptive_k": self.use_adaptive_k,
        }

    @classmethod
    def get_param_schema(cls) -> list[dict]:
        return [
            {"name": "k", "type": "float", "default": 0.5, "description": "변동성 돌파 K값 (0~1, 고정 시 사용)"},
            {"name": "ma_period", "type": "int", "default": 20, "description": "이동평균 기간 (일)"},
            {"name": "trailing_stop_pct", "type": "float", "default": 3.0, "description": "트레일링 스톱 비율 (%)"},
            {"name": "use_adaptive_k", "type": "bool", "default": True, "description": "전일 변동성 기반 적응형 K 사용"},
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
            prev_close = float(prev_day["stck_clpr"])
            today_open = float(daily_data[0]["stck_oprc"])

            # 갭 필터: 당일 시가가 전일 종가 대비 +ENTRY_GAP_UP_PCT% 이상 갭업이면 진입 스킵
            gap_up_pct = getattr(settings, "ENTRY_GAP_UP_PCT", 5.0) or 0
            if gap_up_pct > 0 and prev_close > 0 and today_open >= prev_close * (1 + gap_up_pct / 100):
                logger.debug(f"[{symbol}] 갭업 필터 스킵 (시가 {today_open:.0f} >= 전일종가*{1+gap_up_pct/100:.2f})")
                self.log_decision(symbol, "HOLD", f"갭업 {gap_up_pct}% 이상 진입 스킵", indicators, current_price, "SKIPPED")
                return "HOLD", None

            volatility = prev_high - prev_low
            # 적응형 K: 전일 변동폭/전일 종가 비율에 따라 K 조절
            k_effective = self.k
            if self.use_adaptive_k and prev_close > 0:
                prev_vol_ratio = volatility / prev_close
                if prev_vol_ratio > 0.05:
                    k_effective = 0.3
                elif prev_vol_ratio < 0.02:
                    k_effective = 0.65
                else:
                    k_effective = 0.45
                indicators["k_effective"] = k_effective
                indicators["prev_vol_ratio"] = round(prev_vol_ratio, 4)
            target_price = today_open + volatility * k_effective
            indicators["target_price"] = round(target_price, 2)
            indicators["volatility"] = round(volatility, 2)

            # 거래량 필터: 당일 거래량 >= 직전 20봉 평균 * ENTRY_VOLUME_RATIO (일봉에 거래량 필드 있을 때만)
            volume_ratio_req = getattr(settings, "ENTRY_VOLUME_RATIO", 0) or 0
            if volume_ratio_req > 0 and current_price >= target_price:
                vol_key = None
                for key in ("acml_vol", "stck_acml_vol", "acml_volume"):
                    if daily_data[0].get(key) is not None:
                        vol_key = key
                        break
                if vol_key:
                    try:
                        today_vol = float(daily_data[0][vol_key])
                        vols = [float(d[vol_key]) for d in daily_data[1 : self.ma_period + 1] if d.get(vol_key) is not None]
                        if len(vols) >= self.ma_period:
                            avg_vol_20 = sum(vols) / len(vols)
                            if avg_vol_20 > 0 and today_vol < avg_vol_20 * volume_ratio_req:
                                logger.debug(f"[{symbol}] 거래량 필터 스킵 (당일 {today_vol:.0f} < 20일평균*{volume_ratio_req})")
                                self.log_decision(symbol, "HOLD", f"거래량 부족 (당일 < 20일평균*{volume_ratio_req})",
                                                  indicators, current_price, "SKIPPED")
                                return "HOLD", None
                    except (TypeError, ValueError, KeyError):
                        pass

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
