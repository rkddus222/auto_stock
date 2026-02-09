# FastAPI 백엔드 CORS 설정 제안

FastAPI 백엔드의 `main.py` 파일에 아래 코드를 추가하여 프론트엔드からの 요청을 허용해야 합니다.

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS 미들웨어 추가
origins = [
    "http://localhost",
    "http://localhost:5173", # Vite 기본 포트
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # 모든 HTTP 메소드 허용
    allow_headers=["*"], # 모든 헤더 허용
)

# ... 기존 라우터 및 나머지 코드 ...

```
