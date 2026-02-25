from app.api.kis_auth import kis_auth
from app.api.kis_http import kis_get
from app.api.kis_retry import kis_retry, rate_limited
from app.core.logger import logger
from app.core.exceptions import APIRequestError


@kis_retry
@rate_limited
def get_current_price(symbol: str) -> float:
    """주식 현재가를 조회합니다."""
    path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    url = f"{kis_auth.base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": "FHKST01010100"
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": symbol,
    }

    try:
        response = kis_get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()["output"]
            return float(data["stck_prpr"])
        else:
            raise APIRequestError(f"현재가 조회 실패: {response.text}")
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"현재가 조회 중 에러: {e}")
        raise APIRequestError(str(e))


@kis_retry
@rate_limited
def get_daily_ohlcv(symbol: str, days: int = 30):
    """일봉 데이터를 조회합니다 (OHLCV)."""
    path = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    url = f"{kis_auth.base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": "FHKST01010400"
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": symbol,
        "fid_org_adj_prc": "1",  # 수정주가
        "fid_period_div_code": "D"  # 일봉
    }

    try:
        response = kis_get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            # KIS API: 일봉 데이터는 output에 배열로 반환 (output2 아님)
            out = data.get("output2") or data.get("output")
            if out is None:
                raise APIRequestError("일봉 데이터 응답에 output/output2 없음")
            return out if isinstance(out, list) else [out]
        else:
            raise APIRequestError(f"일봉 데이터 조회 실패: {response.text}")
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"일봉 데이터 조회 중 에러: {e}")
        raise APIRequestError(str(e))


@kis_retry
@rate_limited
def get_index_price(index_code: str = "1001") -> float:
    """업종 지수 현재가를 조회합니다. 기본값 1001=코스닥, 0001=코스피."""
    path = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
    url = f"{kis_auth.base_url}{path}"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": "FHPUP02110000",
    }
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": index_code,
    }
    try:
        response = kis_get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json().get("output", {})
            return float(data.get("bstp_nmix_prpr", 0))
        else:
            raise APIRequestError(f"지수 현재가 조회 실패: {response.text}")
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"지수 현재가 조회 중 에러: {e}")
        raise APIRequestError(str(e))


@kis_retry
@rate_limited
def get_index_daily(index_code: str = "1001", days: int = 10) -> list:
    """업종 지수 일봉 데이터를 조회합니다. 기본값 1001=코스닥."""
    path = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
    url = f"{kis_auth.base_url}{path}"
    from datetime import datetime, timedelta, timezone
    KST = timezone(timedelta(hours=9))
    end_date = datetime.now(KST).strftime("%Y%m%d")
    start_date = (datetime.now(KST) - timedelta(days=days + 10)).strftime("%Y%m%d")
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {kis_auth.access_token}",
        "appKey": kis_auth._app_key,
        "appSecret": kis_auth._app_secret,
        "tr_id": "FHKUP03500100",
    }
    params = {
        "fid_cond_mrkt_div_code": "U",
        "fid_input_iscd": index_code,
        "fid_input_date_1": start_date,
        "fid_input_date_2": end_date,
        "fid_period_div_code": "D",
    }
    try:
        response = kis_get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            out = data.get("output2") or data.get("output1") or data.get("output")
            if out and isinstance(out, list):
                return out[:days]
            return []
        else:
            raise APIRequestError(f"지수 일봉 조회 실패: {response.text}")
    except APIRequestError:
        raise
    except Exception as e:
        logger.error(f"지수 일봉 조회 중 에러: {e}")
        raise APIRequestError(str(e))
