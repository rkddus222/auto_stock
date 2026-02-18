"""기술적 지표 계산 (ATR 등)."""
from app.api import kis_market
from app.core.config import settings


def compute_atr_from_daily(daily_data: list, period: int = 20) -> float | None:
    """
    일봉 리스트에서 ATR(평균 진폭)을 계산합니다.
    daily_data: [오늘, 전일, ...] 순서. 각 항목은 stck_hgpr, stck_lwpr, stck_clpr 포함.
    """
    if not daily_data or len(daily_data) < period + 1:
        return None
    tr_list = []
    for i in range(min(period + 1, len(daily_data) - 1)):
        high = float(daily_data[i]["stck_hgpr"])
        low = float(daily_data[i]["stck_lwpr"])
        close = float(daily_data[i]["stck_clpr"])
        prev_close = float(daily_data[i + 1]["stck_clpr"]) if i + 1 < len(daily_data) else close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    return sum(tr_list[:period]) / period


def get_atr(symbol: str, period: int | None = None) -> float | None:
    """종목의 ATR(period일)을 조회합니다. 실패 시 None."""
    period = period or getattr(settings, "ATR_PERIOD", 20)
    try:
        data = kis_market.get_daily_ohlcv(symbol, days=period + 2)
        return compute_atr_from_daily(data, period)
    except Exception:
        return None
