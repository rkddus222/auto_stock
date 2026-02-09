import time
import threading
from functools import wraps

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.core.exceptions import APIRequestError
from app.core.logger import logger

# 재시도 데코레이터: 3회 시도, 지수 백오프 2~10초
kis_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((APIRequestError, ConnectionError, TimeoutError)),
    before_sleep=lambda retry_state: logger.warning(
        f"KIS API 재시도 {retry_state.attempt_number}/3: {retry_state.outcome.exception()}"
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
