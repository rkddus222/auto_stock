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
- **환경변수**: pydantic-settings (.env)

---

## 설정 방법

### 1. 환경변수 설정

`.env.example` 파일을 복사하여 `.env` 파일을 생성한 뒤, 본인의 API 키·계좌번호 등을 입력합니다.

```bash
# Windows (PowerShell)
copy .env.example .env

# Linux / macOS
cp .env.example .env
```

**필수 항목**만 채워도 동작합니다. 나머지는 선택이며, 넣지 않으면 기본값이 적용됩니다.

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

### 3. 서버 실행

```bash
uvicorn app.main:app --reload
```

---

## 환경 변수 전체 목록

### 필수 (반드시 설정)

| 변수명 | 설명 |
|--------|------|
| `KIS_APP_KEY` | 한국투자증권 Open API 앱 키 |
| `KIS_APP_SECRET` | 한국투자증권 Open API 앱 시크릿 |
| `KIS_ACCOUNT_NO` | 계좌번호 |
| `SLACK_WEBHOOK_URL` | Slack 알림용 Webhook URL |

### 거래 모드

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `MOCK_TRADE` | `True` | `True`=모의거래, `False`=실제 거래 |

### 전략·종목

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `VOLATILITY_BREAKOUT_K` | `0.5` | 변동성 돌파 K 값 (0~1) |
| `USE_ADAPTIVE_K` | `True` | 전일 변동성 기반 적응형 K 사용 여부 |
| `BUDGET_RATIO` | `0.5` | 매매 시 예수금 사용 비율 (0~1, 예: 0.5=50%) |
| `TARGET_SYMBOLS` | `"005930,000660"` | 대상 종목 코드 (쉼표 구분). 고정 종목 사용 시에만 사용 |

### 종목 소스 (거래량/조건검색)

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `USE_VOLUME_RANK` | `False` | `True` 시 거래량 상위 API로 종목 발굴 (HTS 불필요) |
| `USE_CONDITION_SEARCH` | `False` | `True` 시 HTS 조건검색 결과로 종목 갱신 |
| `KIS_USER_ID` | `""` | HTS 로그인 ID (조건검색 사용 시 필수) |
| `CONDITION_SEARCH_SEQ` | `"0"` | 사용할 조건식 순번 (0=첫 번째 저장 조건) |
| `CONDITION_SEARCH_MAX` | `10` | 조건검색/거래량 순위에서 가져올 최대 종목 수 |
| `CONDITION_MIN_PRICE` | `1000` | 가격 필터 최소값(원). 동전주 제외용 |
| `CONDITION_MAX_PRICE` | `99999999` | 가격 필터 최대값(원). 예: 20000 = 2만 원 이하만 |
| `BLACKLIST_SYMBOLS` | `""` | 제외할 종목 코드 (쉼표 구분) |

### 슬롯·재검색

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `MAX_SLOTS` | `3` | 최대 동시 보유 종목 수 |
| `SCAN_INTERVAL` | `60` | 빈 자리 발견 시 재검색 간격(초) |

### 진입 필터 (시간·갭·거래량)

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `ENTRY_NO_BEFORE_MINUTE` | `30` | 이 분 전에는 신규 매수 금지 (예: 30 → 09:30 이후만) |
| `ENTRY_NO_AFTER_HOUR` | `14` | 이 시각 이후 신규 매수 금지 (예: 14 = 14:00) |
| `ENTRY_NO_AFTER_MINUTE` | `30` | 14:30이면 HOUR=14, MINUTE=30 |
| `ENTRY_GAP_UP_PCT` | `5.0` | 전일 대비 갭업 이 % 이상이면 진입 스킵 (0=미적용) |
| `ENTRY_VOLUME_RATIO` | `1.5` | 돌파 시점 거래량 ≥ 직전 20봉 평균×이 값일 때만 진입 (0=미적용) |

### 일별 리스크 관리

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `DAILY_LOSS_LIMIT_PCT` | `-2.0` | 당일 실현손실이 총자산 대비 이 % 이하면 신규 매수 중단 |
| `MAX_CONSECUTIVE_LOSSES` | `3` | 이 횟수 연패 시 다음 매수 예산 축소 |
| `BUDGET_CUT_ON_STREAK` | `0.5` | 연패 시 적용할 예산 비율 (0.5=50%) |
| `MAX_DAILY_TRADES` | `6` | 당일 체결 건수 이하면 신규 매수 허용 (0=제한 없음) |

### ATR 손절 (변동성 돌파)

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `USE_ATR_STOP` | `False` | `True` 시 ATR 기반 손절, `False` 시 고정 % 손절 |
| `ATR_PERIOD` | `20` | ATR 계산 기간 |
| `ATR_MULTIPLIER` | `1.5` | ATR 배수 |

### 종목 스코어링

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `USE_STOCK_SCORING` | `False` | `True` 시 후보를 스코어로 정렬 후 상위 `MAX_SLOTS`만 진입 |

### 기타

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `DATA_DIR` | `""` | DB·상태 파일 기준 디렉터리 (비우면 프로젝트 루트) |

---

상세 기본값과 타입은 `app/core/config.py`를 참고하면 됩니다.
