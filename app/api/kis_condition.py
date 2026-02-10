"""
한국투자증권 단타 대상 종목 발굴: HTS 조건검색 + 거래량 순위 API.
- 조건검색: HTS에 저장한 조건식 결과 조회 (psearch).
- 거래량 순위: volume-rank API로 상위 종목 조회 후 파이썬에서 필터링 (HTS 불필요).
"""
from app.api.kis_auth import kis_auth
from app.api.kis_http import kis_get
from app.api.kis_retry import kis_retry, rate_limited
from app.core.config import settings
from app.core.logger import logger
from app.core.exceptions import APIRequestError


def _condition_tr_id() -> str:
    """조건검색 API tr_id: 모의 VHKST03900400, 실전 HHKST03900400 (KIS 문서 확인 권장)"""
    return "VHKST03900400" if settings.MOCK_TRADE else "HHKST03900400"


@kis_retry
@rate_limited
def get_condition_titles() -> list[dict]:
    """
    HTS에 저장된 조건검색 식 목록을 조회합니다.
    반환: [{"seq": "0", "title": "조건명"}, ...]
    """
    path = "/uapi/domestic-stock/v1/quotations/psearch-title"
    url = f"{kis_auth.base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": _condition_tr_id(),
        "custtype": "P",
    }
    try:
        response = kis_get(url, headers=headers)
        if response.status_code != 200:
            raise APIRequestError(f"조건식 목록 조회 실패: {response.text}")
        data = response.json()
        # 응답 구조는 KIS 문서 기준 (output 또는 output1 등)
        out = data.get("output") or data.get("output1") or data.get("output2")
        if out is None:
            return []
        rows = out if isinstance(out, list) else [out]
        result = []
        for i, row in enumerate(rows):
            if isinstance(row, dict):
                seq = str(row.get("seq", row.get("idx", i)))
                title = row.get("title", row.get("name", ""))
                result.append({"seq": seq, "title": title})
            else:
                result.append({"seq": str(i), "title": str(row)})
        return result
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"조건식 목록 조회 중 에러: {e}")
        raise APIRequestError(str(e))


@kis_retry
@rate_limited
def get_condition_result(user_id: str, seq: str) -> list[dict]:
    """
    지정한 조건식의 검색 결과(종목 리스트)를 조회합니다.
    반환: [{"code": "005930", "name": "삼성전자", ...}, ...]
    """
    path = "/uapi/domestic-stock/v1/quotations/psearch-result"
    url = f"{kis_auth.base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": _condition_tr_id(),
        "custtype": "P",
    }
    params = {
        "user_id": user_id,
        "seq": seq,
    }
    try:
        response = kis_get(url, headers=headers, params=params)
        if response.status_code != 200:
            raise APIRequestError(f"조건검색 결과 조회 실패: {response.text}")
        data = response.json()
        out = data.get("output2") or data.get("output") or data.get("output1")
        if out is None:
            return []
        rows = out if isinstance(out, list) else [out]
        result = []
        for row in rows:
            if isinstance(row, dict):
                code = row.get("code", row.get("mksc_shrn_iscd", row.get("종목코드", "")))
                if code:
                    name = row.get("name", row.get("종목명", ""))
                    result.append({"code": code.strip(), "name": name})
            else:
                continue
        return result
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"조건검색 결과 조회 중 에러: {e}")
        raise APIRequestError(str(e))


def get_target_stocks_by_condition() -> list[str]:
    """
    설정(USE_CONDITION_SEARCH, KIS_USER_ID, CONDITION_SEARCH_SEQ)에 따라
    HTS 조건검색 결과를 조회하고, 블랙리스트·동전주 필터를 적용해 종목 코드 리스트를 반환합니다.
    실패 시 빈 리스트를 반환합니다.
    """
    from app.api import kis_market

    user_id = getattr(settings, "KIS_USER_ID", "") or ""
    if not user_id:
        logger.warning("조건검색 사용 시 KIS_USER_ID가 필요합니다.")
        return []

    seq = settings.CONDITION_SEARCH_SEQ or "0"
    max_count = settings.CONDITION_SEARCH_MAX or 10
    blacklist = set(settings.blacklist_symbols_list)
    min_price = settings.CONDITION_MIN_PRICE or 1000

    try:
        rows = get_condition_result(user_id, seq)
    except Exception as e:
        logger.error(f"조건검색 결과 조회 실패: {e}")
        return []

    codes = []
    for r in rows:
        code = (r.get("code") or "").strip()
        if not code or code in blacklist:
            continue
        codes.append(code)

    # 동전주 제외: 현재가가 min_price 미만인 종목은 제외하고, 상위부터 가격 확인해 max_count개까지 채움
    filtered = []
    for code in codes:
        if len(filtered) >= max_count:
            break
        try:
            price = kis_market.get_current_price(code)
            if price >= min_price:
                filtered.append(code)
            else:
                logger.debug(f"조건검색 종목 제외(동전주): {code} 현재가 {price:.0f}")
        except Exception as e:
            logger.debug(f"현재가 조회 실패로 종목 제외: {code} - {e}")

    logger.info(f"조건검색 대상 종목 수: {len(filtered)} (원본 {len(rows)}건, 블랙리스트·동전주 필터 후)")
    return filtered


# --- 거래량 순위 API (HTS 조건검색 대안) ---

@kis_retry
@rate_limited
def get_top_volume_stocks() -> list[str]:
    """
    [대안] HTS 조건검색 없이 '거래량 상위' API로 종목 발굴.
    서버에서 1000원 이상·거래량 1만주 이상 등으로 필터링하고,
    Python에서 우선주 제외·블랙리스트·최대 개수 적용.
    """
    min_price = settings.CONDITION_MIN_PRICE or 1000
    max_price = settings.CONDITION_MAX_PRICE or 99999999

    path = "/uapi/domestic-stock/v1/quotations/volume-rank"
    url = f"{kis_auth.base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": "FHPST01710000",  # 거래량 순위 조회 (실전 동일 사용. output 없으면 장시간/파라미터 확인)
        "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",   # J: 전체, P: 코스피, Q: 코스닥
        "FID_COND_SCR_DIV_CODE": "20171", # 거래량 순위 화면번호
        "FID_INPUT_ISCD": "0000",        # 0000: 전체
        "FID_DIV_CLS_CODE": "0",         # 0: 전체
        "FID_BLNG_CLS_CODE": "0",        # 0: 평균거래량
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": str(min_price),   # 최소 가격 (동전주 제외)
        "FID_INPUT_PRICE_2": str(max_price),   # 최대 가격 (잔고에 맞게 설정)
        "FID_VOL_CNT": "10000",          # 거래량 1만주 이상
        "FID_INPUT_DATE_1": "",
    }
    try:
        response = kis_get(url, headers=headers, params=params)
        if response.status_code != 200:
            raise APIRequestError(f"거래량 순위 조회 실패: {response.status_code} {response.text}")
        data = response.json()
        rt_cd = data.get("rt_cd", "")
        if rt_cd != "0" and rt_cd != "":
            msg = data.get("msg1", data.get("msg_cd", str(data)))
            logger.warning("거래량 순위 API 오류: rt_cd=%s %s", rt_cd, msg)
            return []
        # output 키 탐색: [] 빈 리스트도 유효하므로 is None으로 체크
        out = data.get("output")
        if out is None:
            out = data.get("output1")
        if out is None:
            out = data.get("output2")
        if out is None:
            body = data.get("body") or {}
            out = body.get("output") or body.get("output1") or body.get("output2")
        if not out:
            msg1 = data.get("msg1", "")
            msg_cd = data.get("msg_cd", "")
            logger.warning(
                "거래량 순위 API output 비어있음. msg_cd=%s msg1=%s raw_output=%s (전체키: %s)",
                msg_cd,
                msg1 or "(비어있음)",
                repr(data.get("output")),
                list(data.keys()),
            )
            return []
        rows = out if isinstance(out, list) else [out]
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"거래량 순위 조회 중 에러: {e}")
        raise APIRequestError(str(e))

    max_count = settings.CONDITION_SEARCH_MAX or 10
    blacklist = set(settings.blacklist_symbols_list)
    max_price = settings.CONDITION_MAX_PRICE or 99999999
    stock_list: list[str] = []

    for item in rows:
        if len(stock_list) >= max_count:
            break
        if not isinstance(item, dict):
            continue
        code = (item.get("mksc_shrn_iscd") or item.get("code") or "").strip()
        if not code or code in blacklist:
            continue
        # 우선주 제외 (종목코드 끝이 0이 아닌 경우 제외, 예: 005935)
        if len(code) > 0 and code[-1] != "0":
            continue
        try:
            price = int(item.get("stck_prpr", 0) or 0)
        except (TypeError, ValueError):
            price = 0
        if price < min_price:
            logger.debug(f"거래량순위 종목 제외(최소가격): {code} 현재가 {price}")
            continue
        if price > max_price:
            logger.debug(f"거래량순위 종목 제외(최대가격): {code} 현재가 {price}")
            continue
        stock_list.append(code)

    logger.info(f"거래량 순위 대상 종목 수: {len(stock_list)} (상위 {max_count}개, 우선주·블랙리스트·가격 필터 후)")
    return stock_list
