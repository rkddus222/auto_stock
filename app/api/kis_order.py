from app.api.kis_auth import kis_auth
from app.api.kis_http import kis_get, kis_post
from app.api.kis_retry import kis_retry, rate_limited
from app.core.config import settings
from app.core.logger import logger
from app.core.exceptions import OrderError, APIRequestError


def _balance_tr_id() -> str:
    """잔고조회 tr_id: 모의투자 VTTC8434R, 실전 TTTC8434R"""
    return "VTTC8434R" if settings.MOCK_TRADE else "TTTC8434R"


def _get_account_parts():
    """계좌번호에서 CANO(8자리), ACNT_PRDT_CD(2자리) 반환. API INPUT_FIELD_SIZE 제한 준수."""
    s = (settings.KIS_ACCOUNT_NO or "").strip().replace(" ", "")
    parts = s.split("-")
    if len(parts) >= 2:
        cano = (parts[0] or "").strip()[:8]
        acnt_prdt_cd = ((parts[1] or "").strip()[:2] or "01").zfill(2)
    else:
        num = (parts[0] or "").strip()
        if len(num) == 10:
            cano = num[:8]
            acnt_prdt_cd = num[8:10]
        elif len(num) == 8:
            cano = num
            acnt_prdt_cd = "01"
        else:
            cano = num[:8]
            acnt_prdt_cd = (num[8:10] if len(num) > 8 else "01").zfill(2)[:2]
    return cano, acnt_prdt_cd


@kis_retry
@rate_limited
def place_order(symbol: str, quantity: int, price: int, order_type: str):
    """시장가/지정가 주문을 실행합니다."""
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    url = f"{kis_auth.base_url}{path}"

    order_code = "00" if price > 0 else "01"
    if settings.MOCK_TRADE:
        tr_id = "VTTC0802U" if order_type == "BUY" else "VTTC0801U"
    else:
        tr_id = "TTTC0802U" if order_type == "BUY" else "TTTC0801U"
    cano, acnt_prdt_cd = _get_account_parts()

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO": symbol,
        "ORD_DVSN": order_code,
        "ORD_QTY": str(quantity),
        "ORD_UNPR": str(price),
    }

    try:
        response = kis_post(url, headers=headers, json=body)
        res = response.json()
        if res["rt_cd"] == "0":
            logger.debug(f'주문 성공: {res["msg1"]}')
            return res
        else:
            msg1 = res.get("msg1", "주문 실패")
            try:
                path_balance = "/uapi/domestic-stock/v1/trading/inquire-balance"
                url_balance = f"{kis_auth.base_url}{path_balance}"
                cano, acnt_prdt_cd = _get_account_parts()
                headers_balance = {
                    "Content-Type": "application/json",
                    "authorization": f"Bearer {kis_auth.access_token}",
                    "appKey": kis_auth._app_key,
                    "appSecret": kis_auth._app_secret,
                    "tr_id": _balance_tr_id(),
                }
                base_params = {
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "01",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                }
                bal = None
                for inqr_dvsn in ("02", "01"):  # 02=요약(계좌 전체), 01=주식별; 요약이 더 정확할 수 있음
                    params_balance = {**base_params, "INQR_DVSN": inqr_dvsn}
                    resp = kis_get(url_balance, headers=headers_balance, params=params_balance)
                    bal = resp.json()
                    if bal.get("rt_cd") == "0":
                        break
                price_str = f"{price:,}원" if price and price > 0 else "시장가"
                side = "매수" if order_type == "BUY" else "매도"
                if bal and bal.get("rt_cd") == "0":
                    summary = _parse_balance_summary(bal)
                    logger.warning(
                        f"주문 실패: {msg1}. 주문 시도: {symbol} {quantity}주 @ {price_str} ({side}). "
                        f"예수금: {summary['deposit']:,}원, 주문가능금액({summary['orderable_source']}): {summary['orderable']:,}원"
                    )
                else:
                    logger.warning(f"주문 실패: {msg1}. 주문 시도: {symbol} {quantity}주 @ {price_str} ({side}). (잔고 조회 실패)")
            except Exception as e2:
                price_str = f"{price:,}원" if price and price > 0 else "시장가"
                side = "매수" if order_type == "BUY" else "매도"
                logger.warning(f"주문 실패: {msg1}. 주문 시도: {symbol} {quantity}주 @ {price_str} ({side}). 잔고 조회 중 오류: {e2}")
            raise OrderError(f'주문 실패: {msg1}')
    except (OrderError, APIRequestError):
        raise
    except Exception as e:
        logger.error(f"주문 중 에러 발생: {e}")
        raise APIRequestError(str(e))


@kis_retry
@rate_limited
def get_balance():
    """주식 잔고를 조회합니다."""
    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    url = f"{kis_auth.base_url}{path}"
    cano, acnt_prdt_cd = _get_account_parts()

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": _balance_tr_id(),
    }
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "01",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }

    try:
        response = kis_get(url, headers=headers, params=params)
        res = response.json()
        if res["rt_cd"] == "0":
            return res["output1"]
        else:
            raise APIRequestError(f'잔고 조회 실패: {res["msg1"]}')
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"잔고 조회 중 에러: {e}")
        raise APIRequestError(str(e))


def _parse_cash_from_balance_response(res: dict) -> int:
    """잔고조회 응답에서 예수금총금액을 추출. output2가 비어 있으면 0 반환."""
    out2 = res.get("output2")
    if not out2 or not isinstance(out2, list) or len(out2) == 0:
        return 0
    row = out2[0]
    amt = row.get("dnca_tot_amt") or row.get("tot_evlu_amt") or row.get("prvs_rcdl_excc_amt") or "0"
    return int(amt)


def _parse_orderable_from_balance_response(res: dict) -> int:
    """잔고조회 응답에서 주문가능금액(ord_psbl_cash) 또는 예수금을 추출. output2가 비어 있으면 0 반환."""
    summary = _parse_balance_summary(res)
    return summary["orderable"]


# 잔고 output2에서 주문가능금액: ord_psbl_cash(주문가능현금) = 주문 가능한 원화 (KIS inquire-balance)
_ORDERABLE_KEYS = (
    "ord_psbl_cash",  # 주문가능현금 (주문 가능 원화)
    "ord_psbl_won",
    "ord_psbl_amt",
    "ord_psbl_krw",
)


def _parse_balance_summary(res: dict) -> dict:
    """
    잔고조회 output2에서 예수금·주문가능금액을 추출.
    주문가능원화(ord_psbl_won) 등 후보 필드를 순서대로 시도.
    반환: {"deposit": 예수금총금액, "orderable": 주문가능금액, "orderable_source": 사용한 필드명}
    """
    out2 = res.get("output2")
    if not out2 or not isinstance(out2, list) or len(out2) == 0:
        return {"deposit": 0, "orderable": 0, "orderable_source": "없음"}
    row = out2[0]
    logger.debug(f"잔고 output2[0] 필드: {list(row.keys())}")
    deposit = int(row.get("dnca_tot_amt") or row.get("tot_evlu_amt") or row.get("prvs_rcdl_excc_amt") or "0")
    orderable = deposit
    source = "dnca_tot_amt(예수금)"
    for key in _ORDERABLE_KEYS:
        val = row.get(key)
        if val is not None and str(val).strip() != "":
            try:
                orderable = int(val)
                source = key
                break
            except (TypeError, ValueError):
                continue
    return {"deposit": deposit, "orderable": orderable, "orderable_source": source}


@kis_retry
@rate_limited
def get_cash_balance():
    """계좌의 현금 예수금을 조회합니다. 요약(02)이 비면 주식별(01)로 재조회."""
    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    url = f"{kis_auth.base_url}{path}"
    cano, acnt_prdt_cd = _get_account_parts()
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": _balance_tr_id(),
    }
    base_params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    # 모의투자에서는 INQR_DVSN "02"(요약)가 INVALID_CHECK_INQR_DVSN 오류를 일으킬 수 있어 "01"(주식별) 먼저 시도
    try:
        params_01 = {**base_params, "INQR_DVSN": "01"}
        response = kis_get(url, headers=headers, params=params_01)
        res = response.json()
        if res.get("rt_cd") != "0":
            raise APIRequestError(res.get("msg1", "예수금 조회 실패"))
        cash = _parse_cash_from_balance_response(res)
        if cash > 0:
            return cash
        params_02 = {**base_params, "INQR_DVSN": "02"}
        response = kis_get(url, headers=headers, params=params_02)
        res = response.json()
        if res.get("rt_cd") != "0":
            raise APIRequestError(res.get("msg1", "예수금 조회 실패"))
        cash = _parse_cash_from_balance_response(res)
        return cash
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"예수금 조회 중 에러: {e}")
        raise APIRequestError(str(e))
