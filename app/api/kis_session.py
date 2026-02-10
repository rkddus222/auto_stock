"""
한국투자증권 API 서버 인증서가 OpenSSL 3.x에서 'EE certificate key too weak'로 거부되는 문제를
피하기 위해, KIS 요청에만 SECLEVEL=1 SSL 컨텍스트를 사용하는 세션을 제공합니다.
"""
import ssl
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


class _KISSSLAdapter(HTTPAdapter):
    """SECLEVEL=1을 사용해 약한 인증서를 허용하는 HTTPS 어댑터 (KIS API 전용)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def get_kis_session() -> requests.Session:
    """KIS API 호출용 Session (SSL 보안 수준 완화 적용)."""
    session = requests.Session()
    session.mount("https://", _KISSSLAdapter())
    return session


# 모듈 로드 시 한 번만 생성해 재사용 (연결 풀·레이트 리미트와 함께 사용)
_kis_session: requests.Session | None = None


def kis_session() -> requests.Session:
    global _kis_session
    if _kis_session is None:
        _kis_session = get_kis_session()
    return _kis_session
