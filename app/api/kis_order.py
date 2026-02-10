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
            raise OrderError(f'주문 실패: {res["msg1"]}')
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
