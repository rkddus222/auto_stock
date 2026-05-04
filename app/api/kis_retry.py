import time
import threading
from functools import wraps

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.core.exceptions import APIRequestError
from app.core.logger import logger

# 재시도 대상 예외:
# - APIRequestError: KIS 응답 rt_cd != 0 등 비즈니스 실패
# - requests.exceptions.ConnectionError: DNS 해석 실패(NameResolutionError),
#   서버 연결 끊김(RemoteDisconnected) 등을 모두 포함
# - requests.exceptions.Timeout: read/connect 타임아웃
# - 빌트인 ConnectionError/TimeoutError: 하위 라이브러리에서 직접 raise하는 경우 대비
_RETRYABLE_EXCEPTIONS = (
    APIRequestError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    ConnectionError,
    TimeoutError,
)

# 재시도 데코레이터: 5회 시도, 지수 백오프 2~30초
# DNS 일시 장애가 수십 초 지속되는 케이스 대응을 위해 시도 횟수와 최대 대기를 늘림
kis_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
    before_sleep=lambda retry_state: logger.warning(
        f"KIS API 재시도 {retry_state.attempt_number}/5: "
        f"{type(retry_state.outcome.exception()).__name__}: {retry_state.outcome.exception()}"
    ),
    reraise=True,
)

# 레이트 리미터: 요청 간 최소 0.25초 간격
_rate_lock = threading.Lock()
_last_request_time = 0.0


def rate_limited(func):
    """요청 간 0.25초 간격을 보장하는 레이트 리미터 데코레이터"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_request_time
        with _rate_lock:
            elapsed = time.time() - _last_request_time
            if elapsed < 0.25:
                time.sleep(0.25 - elapsed)
            _last_request_time = time.time()
        return func(*args, **kwargs)
    return wrapper
