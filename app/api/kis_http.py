"""
한국투자증권 OpenAPI용 HTTP 세션.
서버 인증서가 약한 RSA 키를 사용하여 발생하는 SSL 검증 오류를 우회합니다.
stale connection 방지를 위해 연결 끊김 시 자동 재시도합니다.
"""
import ssl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class _KISHTTPSAdapter(HTTPAdapter):
    """약한 인증서(EE certificate key too weak) 허용 + 연결 끊김 자동 재시도 어댑터."""

    def __init__(self, **kwargs):
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            # stale connection (RemoteDisconnected, ConnectionReset) 자동 재연결
            connect=3,
            read=3,
            other=3,
        )
        kwargs.setdefault("max_retries", retry_strategy)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass  # 구버전 OpenSSL은 SECLEVEL 미지원
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# 모든 KIS API 호출에서 사용할 공용 세션
_kis_session = requests.Session()
_kis_session.mount("https://", _KISHTTPSAdapter())


def kis_get(url: str, **kwargs) -> requests.Response:
    """KIS API GET 요청 (약한 인증서 호환 + 연결 끊김 자동 재시도)."""
    kwargs.setdefault("timeout", (10, 30))
    return _kis_session.get(url, **kwargs)


def kis_post(url: str, **kwargs) -> requests.Response:
    """KIS API POST 요청 (약한 인증서 호환 + 연결 끊김 자동 재시도)."""
    kwargs.setdefault("timeout", (10, 30))
    return _kis_session.post(url, **kwargs)
