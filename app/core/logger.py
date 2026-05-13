
import logging
import logging.handlers
import os
import sys

# 로거 인스턴스 생성
logger = logging.getLogger("autotrade")
logger.setLevel(logging.INFO)

# 포매터 생성
formatter = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")

# 스트림 핸들러 (콘솔 출력)
stream_handler = logging.StreamHandler()
# Windows 콘솔에서 한글 깨짐 방지: stdout/stderr를 UTF-8로 고정
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# 파일 핸들러 (파일 저장)
file_handler = logging.handlers.RotatingFileHandler(
    "autotrade.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
