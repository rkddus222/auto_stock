"""
Gemini LLM 매수 어드바이저 — 전략 BUY 신호를 LLM이 한 번 더 검증한다.
Vertex AI (서비스 계정) 우선, GEMINI_API_KEY 설정 시 직접 API 폴백.
Fail-close 설계: API 실패/파싱 에러 시 매수를 보류한다 (손실 방지 우선).
타임아웃만 fail-open (일시적 네트워크 지연은 기회 손실 방지).
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
_MAX_RETRIES: int = 2  # JSON 파싱 실패 시 재시도 횟수


def _extract_json_from_parts(parts: list[dict]) -> dict | None:
    """Gemini 응답 parts에서 decision JSON을 안정적으로 추출한다."""
    import re

    for part in reversed(parts):
        if part.get("thought"):
            continue
        raw_text = part.get("text", "").strip()
        if not raw_text:
            continue

        # 1차: 그대로 파싱
        try:
            result = json.loads(raw_text)
            if "decision" in result:
                return result
        except json.JSONDecodeError:
            pass

        # 2차: ```json ... ``` 코드 블록 추출
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_text, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(1))
                if "decision" in result:
                    return result
            except json.JSONDecodeError:
                pass

        # 3차: 가장 바깥쪽 { } 블록 추출
        m = re.search(r'\{[^{}]*"decision"\s*:\s*"[^"]*"[^{}]*\}', raw_text)
        if m:
            try:
                result = json.loads(m.group())
                if "decision" in result:
                    return result
            except json.JSONDecodeError:
                pass

        # 4차: 줄바꿈/제어문자 정리 후 재시도
        cleaned = re.sub(r'[\x00-\x1f]+', ' ', raw_text)
        cleaned = re.sub(r',\s*}', '}', cleaned)  # trailing comma 제거
        cleaned = re.sub(r',\s*]', ']', cleaned)
        try:
            result = json.loads(cleaned)
            if "decision" in result:
                return result
        except json.JSONDecodeError:
            pass

    return None


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
    news_text: str = "",
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

    return f"""당신은 한국 주식시장(KRX) **변동성 돌파(Volatility Breakout) 전략** 전문 트레이더입니다.

【전략 특성 — 반드시 숙지】
변동성 돌파 전략은 "당일 시가 + 전일 변동폭 × K"를 돌파할 때 진입하는 **모멘텀 추종** 전략입니다.
따라서 진입 시점에 시가 대비 수 퍼센트 상승은 **정상적인 돌파 신호**이며, 이것만으로 "추격 매수"로 판단해서는 안 됩니다.
핵심 검증 포인트는 ①돌파가 진짜인가(거래량 동반), ②추가 상승 여력이 있는가(과매수 아닌가)입니다.
당신의 역할은 **명백히 위험한 진입만 걸러내는 것**이지, 완벽한 진입만 허용하는 것이 아닙니다.

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

【최근 뉴스】
{news_text if news_text else "뉴스 데이터 없음"}

━━━━━━━━━━ 판단 체크리스트 ━━━━━━━━━━
아래 8개 항목을 평가하되, 각 항목의 **가중치가 다름**에 유의하세요.
★ = 핵심 지표 (이것이 긍정이면 다른 경미한 부정은 상쇄 가능)
○ = 보조 지표 (단독으로 SKIP 사유가 되기 어려움)

★1. **돌파 강도**: 현재가가 목표가(target_price)를 얼마나 상회하는가?
   - 목표가 대비 0.3% 미만 → 가돌파 위험 (부정)
   - 0.3~1% → 보통
   - 1% 이상 → 강한 돌파 (긍정)

★2. **거래량 확인**: 오늘 거래량이 최근 5일 평균 대비 몇 배인가?
   - 1.5배 이상 → 실질 돌파, 강한 긍정
   - 1.0~1.5배 → 보통 (다른 지표 참고)
   - 1.0배 미만 → 거래량 미동반, 부정

○3. **추세 정렬**: 현재가 > 20일 이동평균(MA)인가?
   - MA 위 → 긍정
   - MA 아래 → 약한 부정 (단, 강한 돌파+거래량이면 무시 가능)

○4. **과매수 경계**: RSI 값 확인
   - RSI ≤ 70 → 정상 범위
   - 70 < RSI < 80 → 주의 (돌파 강도가 강하면 진입 가능)
   - RSI ≥ 80 → 과매수, 부정

○5. **당일 상승폭**: 시가 대비 상승률(intraday_change_pct)
   - 10% 미만 → 변동성 돌파의 정상 범위
   - 10~15% → 주의 (거래량 2배 이상이면 허용)
   - 15% 이상 → 과열, 부정

○6. **연속 상승**: 며칠째 연속 상승 중인가?
   - 1~3일 → 모멘텀 초기~중기, 정상
   - 4일 이상 → 차익 실현 압력 가능, 약한 부정
   - 5일 이상 → 부정

○7. **캔들 형태**: 양봉인가? 윗꼬리 비율은?
   - 양봉 + 윗꼬리 40% 미만 → 매수세 우위
   - 윗꼬리 50% 이상 → 매도 압력, 약한 부정

○8. **뉴스 센티먼트**: 최근 뉴스 헤드라인에서 해당 종목에 대한 긍/부정 신호
   - 실적 호조, 수주, 신사업, 외국인 매수 등 → 긍정
   - 실적 부진, 소송, 규제, 대량 매도 등 → 부정
   - 뉴스 없음 또는 중립 → 무시 (판단에 영향 없음)
   - 뉴스만으로 단독 BUY/SKIP 결정 불가, 보조 참고 자료로만 활용

━━━━━━━━━━ 의사결정 규칙 ━━━━━━━━━━
1. ★핵심 지표(돌파 강도 + 거래량)가 **둘 다 긍정**이면 → 보조 지표에 부정이 2~3개 있어도 **BUY** (모멘텀 전략의 핵심이 충족됨)
2. ★핵심 지표 중 하나라도 **부정**이면 → 보조 지표까지 종합 판단
3. 보조 지표(3~8번)에서 **부정이 4개 이상**이면 → SKIP
4. RSI ≥ 80 또는 당일 상승 15% 이상 → 단독 SKIP 사유
5. 판단이 애매하면 → **BUY** (변동성 돌파 전략은 승률보다 손익비가 중요, 손절은 ATR로 관리됨)

━━━━━━━━━━ 응답 형식 ━━━━━━━━━━
아래 JSON만 출력하세요. 다른 텍스트는 절대 포함하지 마세요.
{{"decision": "BUY" 또는 "SKIP", "confidence": 0~100 정수, "reason": "체크리스트 결과 요약 (어떤 항목이 긍정/부정이었는지 간결하게)"}}"""


def _technical_fallback_decision(
    symbol: str,
    current_price: float,
    indicators: dict,
) -> tuple[bool, str]:
    """
    LLM 호출 실패(429 등) 시 기술적 지표만으로 매수 가부를 판단하는 폴백 모드.
    LLM이 차단하던 RSI 과매수 케이스만 거부하고, 그 외는 통과시켜 기회 손실을 최소화한다.
    """
    rsi_block = getattr(settings, "LLM_FALLBACK_RSI_BLOCK", 75.0)
    rsi_val = indicators.get("rsi")
    try:
        rsi_num = float(rsi_val) if rsi_val is not None else None
    except (TypeError, ValueError):
        rsi_num = None
    if rsi_num is not None and rsi_block > 0 and rsi_num >= rsi_block:
        reason = f"폴백: RSI 과매수 차단 (RSI={rsi_num:.1f} >= {rsi_block})"
        _log_decision(symbol, "FALLBACK_SKIP", 0, reason, current_price, "REJECTED")
        return False, reason
    rsi_str = f"{rsi_num:.1f}" if rsi_num is not None else "N/A"
    reason = f"폴백 승인 (LLM 미가용 → 기술 지표만 검증, RSI={rsi_str})"
    _log_decision(symbol, "FALLBACK_BUY", 0, reason, current_price, "APPROVED_FALLBACK")
    return True, reason


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

    # 뉴스 수집 (실패해도 매수 판단에 영향 없음)
    news_text = ""
    try:
        from app.services.news import fetch_stock_news, format_news_for_prompt
        news_list = fetch_stock_news(symbol, max_count=5)
        news_text = format_news_for_prompt(news_list)
    except Exception as e:
        logger.debug(f"[LLM] {symbol} 뉴스 수집 실패 (무시): {e}")

    prompt = _build_prompt(symbol, current_price, indicators, ohlcv_recent, strategy_reason, news_text)

    # 연속 호출 시 최소 간격 보장 (429 방지)
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    last_error_msg = ""
    for attempt in range(_MAX_RETRIES + 1):
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

            # 429 Rate Limit → 폴백 모드 (활성 시) 또는 매수 보류
            if resp.status_code == 429:
                if getattr(settings, "LLM_FALLBACK_ON_429", False):
                    logger.warning(
                        f"[LLM] {symbol} API 요청 한도 초과 (429, {backend}) → 기술 지표 폴백 모드"
                    )
                    return _technical_fallback_decision(symbol, current_price, indicators)
                logger.warning(f"[LLM] {symbol} API 요청 한도 초과 (429, {backend}), 매수 보류")
                _log_decision(symbol, "RATE_LIMITED", 0, f"API 429 ({backend})", current_price, "REJECTED")
                return False, f"LLM API 요청 한도 초과 ({backend}, 매수 보류)"

            resp.raise_for_status()

            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]

            result = _extract_json_from_parts(parts)

            if result is None:
                # 파싱 실패 시 재시도 가능하면 재시도
                if attempt < _MAX_RETRIES:
                    logger.warning(f"[LLM] {symbol} 응답 JSON 파싱 실패, 재시도 ({attempt + 1}/{_MAX_RETRIES})")
                    time.sleep(_MIN_CALL_INTERVAL)
                    continue
                logger.warning(f"[LLM] {symbol} 응답 JSON 파싱 실패 ({_MAX_RETRIES + 1}회 시도), 매수 보류")
                _log_decision(symbol, "PARSE_ERROR", 0, f"응답 JSON 파싱 실패 ({_MAX_RETRIES + 1}회)", current_price, "REJECTED")
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
            last_error_msg = str(e)
            if "key=" in last_error_msg:
                last_error_msg = last_error_msg.split("?")[0] + " (URL 파라미터 생략)"
            if attempt < _MAX_RETRIES:
                logger.warning(f"[LLM] {symbol} API 오류, 재시도 ({attempt + 1}/{_MAX_RETRIES}): {last_error_msg}")
                time.sleep(_MIN_CALL_INTERVAL)
                continue
            logger.warning(f"[LLM] {symbol} API 오류 ({_MAX_RETRIES + 1}회 시도), 매수 보류 (fail-close): {last_error_msg}")
            _log_decision(symbol, "ERROR", 0, last_error_msg[:200], current_price, "REJECTED")
            return False, f"LLM API 오류 (fail-close): {last_error_msg}"

    # 이론상 도달 불가, 안전장치
    return False, "LLM 판단 실패 (fail-close)"
