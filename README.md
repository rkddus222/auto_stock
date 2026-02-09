
# KIS API 기반 주식 자동매매 백엔드 시스템

## 프로젝트 목표
- 한국투자증권(KIS) Open API를 연동하여 주식 잔고 조회, 시세 조회, 매수/매도 주문을 실행합니다.
- '변동성 돌파 전략(Volatility Breakout)' 알고리즘을 구현하여 자동으로 수익을 낼 수 있는 구조를 만듭니다.
- 시스템은 안정적이어야 하며, 모든 거래 로그와 에러를 기록합니다.

## 기술 스택
- **언어**: Python 3.10+
- **프레임워크**: FastAPI
- **데이터베이스**: SQLite + SQLAlchemy
- **스케줄링**: APScheduler
- **알림**: Slack Webhook
- **환경변수**: python-dotenv

## 설정 방법

1.  **환경변수 설정**

    `.env.example` 파일을 복사하여 `.env` 파일을 생성하고, 본인의 API 키, 계좌번호 등을 입력합니다.

    ```bash
    cp .env.example .env
    ```

2.  **패키지 설치**

    ```bash
    pip install -r requirements.txt
    ```

3.  **서버 실행**

    ```bash
    uvicorn app.main:app --reload
    ```
