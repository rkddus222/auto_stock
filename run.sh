#!/bin/bash

# 가상환경이 있다면 활성화 (주석 처리됨, 필요시 주석 해제)
# source venv/bin/activate

echo "자동매매 백엔드 서버를 시작합니다..."
echo "uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

# Uvicorn 서버 실행
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
