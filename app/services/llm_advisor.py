"""
Gemini LLM 매수 어드바이저 — 전략 BUY 신호를 LLM이 한 번 더 검증한다.
Vertex AI (서비스 계정) 우선, GEMINI_API_KEY 설정 시 직접 API 폴백.
Fail-open 설계: API 실패/타임아웃 시 매수를 허용한다 (기회 손실 방지).
429 Rate Limit 시에만 fail-close (매수 보류).
"""

import json
import time
from datetime import date
from pathlib import Path

import requests

from app.core.config import settings
from app.core.logger import logger

# ---------- 일일 호출 카운터 ----------
_daily_call_count: int = 0
_daily_call_date: date | None = None
# ---------- API 호출 간격 제어 ----------
_last_call_ts: float = 0.0
_MIN_CALL_INTERVAL: float = 2.0  # 최소 2초 간격

# ---------- Vertex AI 인증 ----------
_vertex_credentials = None
_vertex_token_expiry: float = 0.0

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
VERTEX_API_URL = "https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models/{model}:generateContent"


def _get_vertex_access_token() -> str | None:
    """서비스 계정 JSON으로 Vertex AI 액세스 토큰을 발급/갱신한다."""
    global _vertex_credentials, _vertex_token_expiry

    # 토큰이 아직 유효하면 재사용
    if _vertex_credentials and time.time() < _vertex_token_expiry:
        return _vertex_credentials.token

    sa_path = Path(settings.VERTEX_SERVICE_ACCOUNT)
    if not sa_path.is_absolute():
        sa_path = Path(settings.base_dir) / sa_path
    if not sa_path.exists():
        logger.warning(f"[LLM] 서비스 계정 파일 없음: {sa_path}")
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        _vertex_credentials = service_account.Credentials.from_service_account_file(
            str(sa_path), scopes=scopes
        )
        _vertex_credentials.refresh(Request())
        # 만료 5분 전에 갱신하도록 여유
        _vertex_token_expiry = time.time() + 3300  # 55분
        logger.debug("[LLM] Vertex AI 액세스 토큰 발급 완료")
        return _vertex_credentials.token
    except Exception as e:
        logger.warning(f"[LLM] Vertex AI 토큰 발급 실패: {e}")
        return None


def _get_vertex_project_id() -> str:
    """설정 또는 서비스 계정 JSON에서 project_id를 가져온다."""
    if settings.VERTEX_PROJECT_ID:
        return settings.VERTEX_PROJECT_ID
    sa_path = Path(settings.VERTEX_SERVICE_ACCOUNT)
    if not sa_path.is_absolute():
        sa_path = Path(settings.base_dir) / sa_path
    try:
        with open(sa_path) as f:
            return json.load(f).get("project_id", "")
    except Exception:
        return ""


def _reset_daily_counter_if_needed() -> None:
    """날짜가 바뀌면 호출 카운터를 리셋한다."""
    global _daily_call_count, _daily_call_date
    today = date.today()
    if _daily_call_date != today:
        _daily_call_count = 0
        _daily_call_date = today


def _derive_ohlcv_metrics(ohlcv: list[dict], current_price: float) -> dict:
    """OHLCV 원시 데이터에서 LLM 판단에 유용한 파생 지표를 계산한다."""
    metrics: dict = {}
    if not ohlcv:
        return metrics

    def _f(d: dict, key: str) -> float:
        return float(d.get(key) or 0)

    def _vol(d: dict) -> float:
        for k in ("acml_vol", "stck_acml_vol", "acml_volume"):
            if d.get(k) is not None:
                return float(d[k])
        return 0.0

    try:
        today = ohlcv[0]
        today_open = _f(today, "stck_oprc")

        # 당일 시가 대비 등락률
        if today_open > 0:
            metrics["intraday_change_pct"] = round(
                (current_price - today_open) / today_open * 100, 2
            )

        # 전일 종가 대비 등락률 (갭 포함)
        if len(ohlcv) > 1:
            prev_close = _f(ohlcv[1], "stck_clpr")
            if prev_close > 0:
                metrics["change_from_prev_close_pct"] = round(
                    (current_price - prev_close) / prev_close * 100, 2
                )
                if today_open > 0:
                    metrics["gap_pct"] = round(
                        (today_open - prev_close) / prev_close * 100, 2
                    )

        # 최근 N일 종가 흐름 → 연속 상승/하락 일수
        closes = [_f(d, "stck_clpr") for d in ohlcv if _f(d, "stck_clpr") > 0]
        if len(closes) >= 2:
            streak = 0
            for i in range(len(closes) - 1):
                if closes[i] > closes[i + 1]:
                    streak += 1
                else:
                    break
            metrics["consecutive_up_days"] = streak

        # 당일 거래량 vs 5일 평균 거래량
        vols = [_vol(d) for d in ohlcv]
        today_vol = vols[0] if vols else 0
        avg_vol = sum(vols[1:6]) / max(len(vols[1:6]), 1) if len(vols) > 1 else 0
        if avg_vol > 0:
            metrics["volume_ratio_vs_5d"] = round(today_vol / avg_vol, 2)
        metrics["today_volume"] = int(today_vol)

        # 최근 5일 고가·저가 범위 대비 현재가 위치 (0%=저점, 100%=고점)
        highs = [_f(d, "stck_hgpr") for d in ohlcv[:5] if _f(d, "stck_hgpr") > 0]
        lows = [_f(d, "stck_lwpr") for d in ohlcv[:5] if _f(d, "stck_lwpr") > 0]
        if highs and lows:
            h, l = max(highs), min(lows)
            if h > l:
                metrics["price_position_in_5d_range_pct"] = round(
                    (current_price - l) / (h - l) * 100, 1
                )

        # 당일 캔들 형태 (양봉/음봉, 윗꼬리·아랫꼬리 비율)
        t_open, t_high, t_low = today_open, _f(today, "stck_hgpr"), _f(today, "stck_lwpr")
        body = current_price - t_open if t_open > 0 else 0
        full_range = t_high - t_low if t_high > t_low else 1
        metrics["candle_body_pct"] = round(abs(body) / full_range * 100, 1)
        metrics["candle_type"] = "양봉" if body >= 0 else "음봉"
        if full_range > 0 and t_high > 0:
            metrics["upper_shadow_pct"] = round(
                (t_high - max(current_price, t_open)) / full_range * 100, 1
            )
    except Exception:
        pass

    return metrics


def _build_prompt(
    symbol: str,
    current_price: float,
    indicators: dict,
    ohlcv_recent: list[dict],
    strategy_reason: str,
) -> str:
    # OHLCV 테이블
    ohlcv_lines = []
    for d in ohlcv_recent[:5]:
        vol = d.get("acml_vol") or d.get("stck_acml_vol") or "?"
        ohlcv_lines.append(
            f"  {d.get('stck_bsop_date','?')} | "
            f"시{d.get('stck_oprc','?')} 고{d.get('stck_hgpr','?')} "
            f"저{d.get('stck_lwpr','?')} 종{d.get('stck_clpr','?')} | "
            f"거래량 {vol}"
        )
    ohlcv_text = "\n".join(ohlcv_lines) if ohlcv_lines else "데이터 없음"

    # 파생 지표
    derived = _derive_ohlcv_metrics(ohlcv_recent, current_price)

    return f"""당신은 한국 주식시장(KRX) 전문 트레이더이자 리스크 매니저입니다.
자동매매 시스템의 변동성 돌파 전략이 아래 종목에 대해 BUY 신호를 발생시켰습니다.
당신의 역할은 이 신호를 **2차 검증**하여 "지금 진입해도 되는가"를 판단하는 것입니다.
매수 후 수분~수시간 보유하는 단타/스윙 관점으로 분석하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ 종목: {symbol}
■ 현재가: {current_price:,.0f}원
■ 전략 매수 근거: {strategy_reason}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【기술적 지표 (전략 산출)】
{json.dumps(indicators, ensure_ascii=False, indent=2)}

【파생 분석 지표 (자동 계산)】
{json.dumps(derived, ensure_ascii=False, indent=2)}

【최근 5일 OHLCV (최신→과거 순)】
{ohlcv_text}

━━━━━━━━━━ 판단 체크리스트 ━━━━━━━━━━
아래 7개 항목을 순서대로 평가하고, 종합하여 최종 판단하세요.

1. **추세 정렬**: 현재가 > 20일 이동평균(MA)인가? MA 위에서 돌파하는 것이 정석.
   - MA 아래에서 돌파 시도 → 신뢰도 낮음

2. **돌파 강도**: 현재가가 목표가(target_price)를 얼마나 상회하는가?
   - 목표가 대비 0.5% 미만 → 가돌파(fake breakout) 위험
   - 1% 이상 상회 → 돌파 신뢰도 높음

3. **거래량 확인**: 오늘 거래량이 최근 5일 평균 대비 몇 배인가?
   - 1.5배 이상 → 돌파에 거래량 동반, 긍정적
   - 1배 미만 → 거래량 미동반 돌파, 지속력 의심

4. **과매수 경계**: RSI 값 확인
   - RSI ≤ 65 → 안전 구간
   - 65 < RSI < 75 → 주의 (다른 지표가 강하면 진입 가능)
   - RSI ≥ 75 → 과매수, SKIP 강력 권고

5. **추격 매수 위험**: 당일 시가 대비 상승률(intraday_change_pct) 확인
   - 5% 미만 → 적정
   - 5~8% → 주의 (확신도 높을 때만 진입)
   - 8% 이상 → 고점 추격 위험 높음, SKIP 권고

6. **연속 상승 피로**: 며칠째 연속 상승 중인가?
   - 1~2일 → 초기 상승, 긍정적
   - 3일 이상 연속 상승 → 차익 매물 출회 가능, 주의

7. **캔들 형태**: 오늘 캔들이 양봉인가? 윗꼬리가 길지 않은가?
   - 양봉 + 윗꼬리 짧음(30% 미만) → 매수세 우위, 긍정적
   - 윗꼬리 40% 이상 → 매도 압력 존재, 주의

━━━━━━━━━━ 의사결정 규칙 ━━━━━━━━━━
- 7개 중 **부정 신호가 3개 이상**이면 → SKIP
- 7개 중 **부정 신호가 2개 이하**이고 돌파 강도 + 거래량이 긍정이면 → BUY
- 판단이 애매하면 → SKIP (보수적 운용 원칙: 놓치는 것보다 잘못 사는 것이 더 나쁘다)

━━━━━━━━━━ 응답 형식 ━━━━━━━━━━
아래 JSON만 출력하세요. 다른 텍스트는 절대 포함하지 마세요.
{{"decision": "BUY" 또는 "SKIP", "confidence": 0~100 정수, "reason": "체크리스트 결과 요약 (어떤 항목이 긍정/부정이었는지 간결하게)"}}"""


def _log_decision(
    symbol: str,
    decision: str,
    confidence: int,
    reason: str,
    current_price: float,
    action_taken: str,
) -> None:
    """DecisionLog 테이블에 LLM 판단 결과를 기록한다."""
    try:
        from app.db import models, session

        db = session.SessionLocal()
        try:
            log = models.DecisionLog(
                symbol=symbol,
                strategy_name="llm_advisor",
                signal=decision,
                decision_reason=reason,
                indicator_values=json.dumps(
                    {"confidence": confidence}, ensure_ascii=False
                ),
                current_price=current_price,
                action_taken=action_taken,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[LLM] DecisionLog 저장 실패: {e}")


def _call_vertex(prompt: str) -> requests.Response:
    """Vertex AI REST API로 Gemini를 호출한다."""
    token = _get_vertex_access_token()
    if not token:
        raise RuntimeError("Vertex AI 액세스 토큰 발급 실패")

    project_id = _get_vertex_project_id()
    if not project_id:
        raise RuntimeError("Vertex AI project_id를 확인할 수 없음")

    region = settings.VERTEX_REGION
    url = VERTEX_API_URL.format(
        region=region, project=project_id, model=settings.GEMINI_MODEL
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 1024},
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    return requests.post(url, json=payload, headers=headers, timeout=60)


def _call_direct_api(prompt: str) -> requests.Response:
    """Gemini 직접 API (API 키 방식) 폴백."""
    url = GEMINI_API_URL.format(model=settings.GEMINI_MODEL)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 1024},
        },
    }
    params = {"key": settings.GEMINI_API_KEY}
    return requests.post(url, json=payload, params=params, timeout=60)


def should_buy(
    symbol: str,
    current_price: float,
    indicators: dict,
    ohlcv_recent: list[dict],
    strategy_reason: str,
) -> tuple[bool, str]:
    """
    Gemini LLM에 매수 여부를 질의한다.
    Vertex AI (서비스 계정) → 직접 API (API 키) 순으로 시도.

    Returns:
        (True, reason)  — 매수 승인
        (False, reason) — 매수 거부
    """
    global _daily_call_count

    if not settings.USE_LLM_ADVISOR:
        return True, "LLM 어드바이저 비활성"

    # Vertex AI도 API 키도 없으면 패스
    sa_path = Path(settings.VERTEX_SERVICE_ACCOUNT)
    if not sa_path.is_absolute():
        sa_path = Path(settings.base_dir) / sa_path
    has_vertex = sa_path.exists()
    has_api_key = bool(settings.GEMINI_API_KEY)

    if not has_vertex and not has_api_key:
        return True, "LLM 어드바이저: 인증 수단 없음 (서비스 계정/API 키 모두 미설정)"

    _reset_daily_counter_if_needed()

    # 일일 호출 한도 초과 → fail-open
    if _daily_call_count >= settings.LLM_MAX_DAILY_CALLS:
        logger.warning(f"[LLM] 일일 호출 한도 초과 ({_daily_call_count}/{settings.LLM_MAX_DAILY_CALLS}), 매수 허용")
        return True, "LLM 일일 호출 한도 초과 (fail-open)"

    prompt = _build_prompt(symbol, current_price, indicators, ohlcv_recent, strategy_reason)

    # 연속 호출 시 최소 간격 보장 (429 방지)
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    try:
        _last_call_ts = time.time()

        # Vertex AI 우선, 실패 시 직접 API 폴백
        resp = None
        used_vertex = False
        if has_vertex:
            try:
                resp = _call_vertex(prompt)
                used_vertex = True
                backend = "Vertex AI"
            except Exception as ve:
                logger.warning(f"[LLM] Vertex AI 호출 실패, 직접 API 폴백: {ve}")
                resp = None

        if resp is None and has_api_key:
            resp = _call_direct_api(prompt)
            backend = "Direct API"

        if resp is None:
            raise RuntimeError("사용 가능한 LLM 백엔드 없음")

        _daily_call_count += 1

        # 429 Rate Limit → 매수 보류 (fail-close)
        if resp.status_code == 429:
            logger.warning(f"[LLM] {symbol} API 요청 한도 초과 (429, {backend}), 매수 보류")
            _log_decision(symbol, "RATE_LIMITED", 0, f"API 429 ({backend})", current_price, "REJECTED")
            return False, f"LLM API 요청 한도 초과 ({backend}, 매수 보류)"

        resp.raise_for_status()

        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]

        # gemini-2.5-flash thinking 모델: parts에 thought + 실제 답변이 분리됨
        # thought가 아닌 마지막 part에서 JSON 추출
        result = None
        for part in reversed(parts):
            if part.get("thought"):
                continue
            raw_text = part.get("text", "")
            try:
                result = json.loads(raw_text)
                break
            except json.JSONDecodeError:
                # JSON 블록이 텍스트에 섞여있을 수 있음
                import re
                m = re.search(r'\{[^{}]*"decision"\s*:\s*"[^"]+?"[^{}]*\}', raw_text)
                if m:
                    result = json.loads(m.group())
                    break

        if result is None:
            logger.warning(f"[LLM] {symbol} 응답 JSON 파싱 실패, 매수 보류")
            _log_decision(symbol, "PARSE_ERROR", 0, "응답 JSON 파싱 실패", current_price, "REJECTED")
            return False, "LLM 응답 파싱 실패 (매수 보류)"

        decision = result.get("decision", "BUY").upper()
        confidence = int(result.get("confidence", 50))
        reason = result.get("reason", "")

        approved = decision == "BUY"
        action = "APPROVED" if approved else "REJECTED"
        _log_decision(symbol, decision, confidence, reason, current_price, action)

        tag = "승인" if approved else "거부"
        logger.info(
            f"[LLM 매수 {tag}] {symbol} | {backend} | 판단={decision}, 확신도={confidence}, 사유={reason}"
        )
        return approved, f"LLM {tag}: {reason} (확신도 {confidence}%)"

    except requests.exceptions.Timeout:
        logger.warning(f"[LLM] {symbol} API 타임아웃, 매수 허용 (fail-open)")
        _log_decision(symbol, "TIMEOUT", 0, "API 타임아웃", current_price, "FAIL_OPEN")
        return True, "LLM API 타임아웃 (fail-open)"
    except Exception as e:
        err_msg = str(e)
        if "key=" in err_msg:
            err_msg = err_msg.split("?")[0] + " (URL 파라미터 생략)"
        logger.warning(f"[LLM] {symbol} API 오류, 매수 허용 (fail-open): {err_msg}")
        _log_decision(symbol, "ERROR", 0, err_msg[:200], current_price, "FAIL_OPEN")
        return True, f"LLM API 오류 (fail-open): {err_msg}"
