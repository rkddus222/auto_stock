"""
종목 스코어링: 거래량 증가율, 전일대비 등락률, MA20 위 거리, 변동성 적정성을 조합해 점수를 매깁니다.
동적 종목(거래량/조건검색) 사용 시 상위 N개만 진입 대상으로 쓰기 위함.
"""
from app.api import kis_market
from app.core.logger import logger
import numpy as np

# 가중치 (합 1.0)
W_VOLUME = 0.3
W_DAILY_CHG = 0.2
W_MA20_DIST = 0.2
W_VOLATILITY = 0.3

MA_PERIOD = 20
VOL_KEY_CANDIDATES = ("acml_vol", "stck_acml_vol", "acml_volume")


def _get_volume_and_ma(symbol: str) -> tuple[float | None, float | None, float | None, float | None]:
    """
    일봉에서 당일 거래량, 20일 평균 거래량, MA20, 전일 종가, 전일 변동폭/종가 비율을 반환.
    반환: (today_vol, avg_vol_20, ma20, prev_close, prev_vol_ratio)
    """
    try:
        data = kis_market.get_daily_ohlcv(symbol, days=MA_PERIOD + 2)
        if not data or len(data) < MA_PERIOD + 1:
            return None, None, None, None, None
        vol_key = None
        for k in VOL_KEY_CANDIDATES:
            if data[0].get(k) is not None:
                vol_key = k
                break
        today_vol = float(data[0][vol_key]) if vol_key else None
        avg_vol_20 = None
        if vol_key:
            vols = [float(d[vol_key]) for d in data[1 : MA_PERIOD + 1] if d.get(vol_key) is not None]
            if len(vols) >= MA_PERIOD:
                avg_vol_20 = sum(vols) / len(vols)
        closes = [float(d["stck_clpr"]) for d in data[1 : MA_PERIOD + 1]]
        ma20 = np.mean(closes)
        prev_close = float(data[1]["stck_clpr"])
        prev_high = float(data[1]["stck_hgpr"])
        prev_low = float(data[1]["stck_lwpr"])
        vol_ratio = (prev_high - prev_low) / prev_close if prev_close > 0 else None
        return today_vol, avg_vol_20, ma20, prev_close, vol_ratio
    except Exception as e:
        logger.debug(f"스코어 데이터 조회 실패 {symbol}: {e}")
        return None, None, None, None, None


def score_symbol(symbol: str) -> float:
    """
    단일 종목에 대해 0~1 스코어를 반환합니다.
    - 거래량_증가율: 당일 거래량 / 20일 평균 (상대적, 1.5면 0.5점 등으로 정규화)
    - 전일대비_등락률: 적당한 양봉이면 가산 (0~5% 구간이면 높게)
    - MA20_위_거리: MA20 바로 위가 좋음 (너무 멀면 과열)
    - 변동성_적정성: 0.02~0.05 구간이면 높게
    """
    try:
        current = kis_market.get_current_price(symbol)
    except Exception:
        return 0.0
    today_vol, avg_vol_20, ma20, prev_close, prev_vol_ratio = _get_volume_and_ma(symbol)
    score_vol = 0.5
    if today_vol is not None and avg_vol_20 is not None and avg_vol_20 > 0:
        ratio = today_vol / avg_vol_20
        score_vol = min(1.0, ratio / 2.0)  # 2배면 1점
    score_daily = 0.5
    if prev_close and prev_close > 0:
        chg = (current - prev_close) / prev_close
        if 0 <= chg <= 0.05:
            score_daily = 0.5 + chg * 10
        elif chg > 0.05:
            score_daily = 1.0
        else:
            score_daily = max(0, 0.5 + chg * 5)
        score_daily = max(0, min(1.0, score_daily))
    score_ma = 0.5
    if ma20 and ma20 > 0 and current > ma20:
        dist = (current - ma20) / ma20
        if 0 < dist <= 0.03:
            score_ma = 0.7 + dist * 10
        elif dist <= 0.05:
            score_ma = 1.0
        else:
            score_ma = max(0.5, 1.0 - (dist - 0.05) * 5)
        score_ma = max(0, min(1.0, score_ma))
    score_volatility = 0.5
    if prev_vol_ratio is not None:
        if 0.02 <= prev_vol_ratio <= 0.05:
            score_volatility = 1.0
        elif prev_vol_ratio < 0.02:
            score_volatility = 0.5 + prev_vol_ratio * 25
        else:
            score_volatility = max(0.3, 1.0 - (prev_vol_ratio - 0.05) * 5)
        score_volatility = max(0, min(1.0, score_volatility))
    total = score_vol * W_VOLUME + score_daily * W_DAILY_CHG + score_ma * W_MA20_DIST + score_volatility * W_VOLATILITY
    return round(total, 4)


def rank_candidates(symbols: list[str], top_n: int) -> list[str]:
    """
    후보 종목에 대해 스코어를 계산하고 상위 top_n개를 반환합니다.
    """
    if not symbols or top_n <= 0:
        return symbols[:top_n]
    scored = [(s, score_symbol(s)) for s in symbols]
    scored.sort(key=lambda x: -x[1])
    return [s for s, _ in scored[:top_n]]
