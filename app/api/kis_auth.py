import requests
import json
import time
from datetime import datetime, timedelta

from app.api.kis_retry import kis_retry, rate_limited
from app.core.config import settings
from app.core.logger import logger
from app.core.exceptions import AuthenticationError

class KISAuth:
    def __init__(self):
        # 모의: openapivts / 29443, 실전: openapi / 9443 (인증서 호스트가 다름)
        self._base_url = "https://openapivts.koreainvestment.com:29443" if settings.MOCK_TRADE else "https://openapi.koreainvestment.com:9443"
        self._app_key = settings.KIS_APP_KEY
        self._app_secret = settings.KIS_APP_SECRET
        self._token_info = self._load_token()

    def _token_path(self):
        return settings.base_dir / "token.json"

    def _load_token(self):
        try:
            with open(self._token_path(), "r", encoding="utf-8") as f:
                token_info = json.load(f)
                expire_time = datetime.strptime(token_info["expire_time"], "%Y-%m-%d %H:%M:%S.%f")
                if expire_time > datetime.now():
                    logger.info("기존 토큰을 재사용합니다.")
                    return token_info
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        logger.info("새로운 토큰을 발급합니다.")
        return self._issue_token()

    def _save_token(self, token_info):
        with open(self._token_path(), "w", encoding="utf-8") as f:
            json.dump(token_info, f, indent=2)

    @kis_retry
    @rate_limited
    def _issue_token(self):
        path = "/oauth2/tokenP"
        url = f"{self._base_url}{path}"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret
        }
        
        response = requests.post(url, headers=headers, json=body)
        if response.status_code != 200:
            raise AuthenticationError(f"토큰 발급 실패: {response.text}")
        
        res = response.json()
        expire_time = datetime.now() + timedelta(seconds=int(res["expires_in"]) - 60) # 1분 여유
        token_info = {
            "access_token": res["access_token"],
            "expire_time": expire_time.strftime("%Y-%m-%d %H:%M:%S.%f")
        }
        self._save_token(token_info)
        logger.info("새로운 접근 토큰이 발급되었습니다.")
        return token_info

    @property
    def access_token(self):
        expire_time = datetime.strptime(self._token_info["expire_time"], "%Y-%m-%d %H:%M:%S.%f")
        if datetime.now() >= expire_time:
            logger.info("토큰이 만료되어 갱신합니다.")
            self._token_info = self._issue_token()
        return self._token_info["access_token"]

    @property
    def base_url(self):
        return self._base_url

kis_auth = KISAuth()
