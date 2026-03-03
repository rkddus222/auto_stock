from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_symbols(v: str) -> list[str]:
    """쉼표로 구분된 종목 코드 문자열을 리스트로 변환"""
    return [s.strip() for s in v.split(",") if s.strip()]


class Settings(BaseSettings):
    KIS_APP_KEY: str
    KIS_APP_SECRET: str
    KIS_ACCOUNT_NO: str
    SLACK_WEBHOOK_URL: str
    MOCK_TRADE: bool = True

    # Strategy settings
    VOLATILITY_BREAKOUT_K: float = 0.5
    USE_ADAPTIVE_K: bool = True   # 변동성 돌파 전략에서 전일 변동성 기반 K 적용
    # 매매 시 예수금의 몇 %를 사용할지 (0~1, 예: 0.5 = 50%)
    BUDGET_RATIO: float = 0.5
    # 대상 종목 코드 (쉼표 구분, 예: "005930,000660"). 조건검색 미사용 시에만 사용
    TARGET_SYMBOLS: str = "005930,000660"

    # 대상 종목 소스: volume_rank(거래량 상위 API) | condition(HTS 조건검색) | 고정(TARGET_SYMBOLS)
    USE_VOLUME_RANK: bool = False   # True 시 거래량 순위 API로 종목 발굴 (HTS 불필요)
    USE_CONDITION_SEARCH: bool = False  # True 시 HTS 조건검색 결과로 갱신 (USE_VOLUME_RANK보다 우선하지 않음)
    # HTS 로그인 ID (조건검색 사용 시 필수)
    KIS_USER_ID: str = ""
    # 사용할 조건식 순번 (0 = 첫 번째 저장 조건)
    CONDITION_SEARCH_SEQ: str = "0"
    # 조건검색에서 가져올 최대 종목 수
    CONDITION_SEARCH_MAX: int = 10
    # 가격 필터: 이 범위만 검색 (원). 잔고에 맞게 상한 설정 권장 (수수료 고려)
    CONDITION_MIN_PRICE: int = 1000   # 최소 (동전주 제외)
    CONDITION_MAX_PRICE: int = 99999999  # 최대 (예: 20000 = 2만 원 이하만)
    # 제외할 종목 코드 (쉼표 구분)
    BLACKLIST_SYMBOLS: str = ""

    # 빈 자리 채우기: 최대 동시 보유 종목 수, 자리 비었을 때 재검색 간격(초)
    MAX_SLOTS: int = 3          # 최대 N종목까지 동시 보유 (자금 관리)
    SCAN_INTERVAL: int = 60     # 빈 자리 발견 시 이 간격(초)마다만 API 재호출 (과다 호출 방지)

    # 진입 필터: 시간(분), 갭업(%), 거래량 비율 (0=미적용)
    ENTRY_NO_BEFORE_MINUTE: int = 30   # 이 시간(분) 전에는 신규 매수 금지 (09:XX → 30 = 09:30 이후만)
    ENTRY_NO_AFTER_HOUR: int = 15      # 이 시각 이후에는 신규 매수 금지 (15 = 15:00)
    ENTRY_NO_AFTER_MINUTE: int = 20    # 15:20 = ENTRY_NO_AFTER_HOUR 15, ENTRY_NO_AFTER_MINUTE 20
    ENTRY_GAP_UP_PCT: float = 5.0      # 당일 시가가 전일 종가 대비 이 비율 이상 갭업이면 진입 스킵 (0=미적용)
    ENTRY_MAX_UP_FROM_OPEN_PCT: float = 10.0  # 당일 시가 대비 현재가가 이 비율 이상 상승했으면 진입 스킵 (국장 상한가 30% 고려, 이미 많이 오른 종목은 하락 리스크 대비 상승 여력 적음, 0=미적용)
    ENTRY_VOLUME_RATIO: float = 1.5    # 돌파 시점 거래량 >= 직전 20봉 평균 * 이 값일 때만 진입 (0=미적용)

    # 일별 리스크 관리
    DAILY_LOSS_LIMIT_PCT: float = -4.0   # 당일 실현손실이 총자산 대비 이 % 이하면 신규 매수 중단
    MAX_CONSECUTIVE_LOSSES: int = 4      # 이 횟수 연패 시 슬롯 축소 + 예산 축소
    BUDGET_CUT_ON_STREAK: float = 0.7    # 연패 시 적용할 예산 비율 (0.7 = 70%)
    MAX_DAILY_TRADES: int = 20           # 당일 체결 건수(BUY+SELL) 이하면 신규 매수 허용 (초과 시 신규 매수만 중단)

    # ATR 손절 (변동성 돌파 전략)
    USE_ATR_STOP: bool = False          # True 시 ATR 기반 손절, False 시 기존 trailing_stop_pct
    ATR_PERIOD: int = 20
    ATR_MULTIPLIER: float = 1.5

    # RSI 이익실현: 보유 중 RSI가 이 값 이상이면 SELL 신호 (0=비활성)
    RSI_EXIT_THRESHOLD: float = 70.0

    # 시장 지수 필터: 지수가 N일 이평 아래면 신규 매수 금지 (0=비활성)
    MARKET_INDEX_CODE: str = "1001"      # 1001=코스닥, 0001=코스피
    MARKET_MA_PERIOD: int = 5            # 지수 이동평균 기간 (일)
    USE_MARKET_FILTER: bool = True       # True 시 지수 MA 필터 적용

    # 매수 주문 방식: 0=시장가, N>0=현재가+N호가 지정가
    BUY_PRICE_TICK_OFFSET: int = 2       # 매수 시 현재가 + N호가 (0=시장가)

    # 종목 스코어링 (동적 종목 사용 시 상위 N개만 진입)
    USE_STOCK_SCORING: bool = False      # True 시 후보를 스코어로 정렬 후 상위 MAX_SLOTS만

    # LLM 매수 어드바이저 (Gemini via Vertex AI)
    USE_LLM_ADVISOR: bool = False        # True 시 BUY 신호 발생 후 LLM 검증 추가
    GEMINI_API_KEY: str = ""             # (레거시) 직접 API 키 방식. 비워두면 Vertex AI 사용
    GEMINI_MODEL: str = "gemini-2.5-flash"
    VERTEX_SERVICE_ACCOUNT: str = "gemini_service_account.json"  # 서비스 계정 JSON 경로
    VERTEX_PROJECT_ID: str = ""          # 비워두면 서비스 계정 JSON에서 자동 추출
    VERTEX_REGION: str = "asia-northeast3" # Vertex AI 리전 (서울)
    LLM_MAX_DAILY_CALLS: int = 50        # 일일 최대 LLM 호출 횟수
    LLM_REJECT_COOLDOWN: int = 1800       # LLM 매수 거부 시 해당 종목 재시도 대기(초), 기본 30분

    # 데이터/상태 파일 기준 디렉터리 (비우면 프로젝트 루트)
    DATA_DIR: str = ""

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def target_symbols_list(self) -> list[str]:
        return _parse_symbols(self.TARGET_SYMBOLS)

    @property
    def blacklist_symbols_list(self) -> list[str]:
        return _parse_symbols(self.BLACKLIST_SYMBOLS)

    @property
    def base_dir(self) -> Path:
        """상태/토큰 파일을 둘 디렉터리 (프로젝트 루트 기준)"""
        return Path(__file__).resolve().parent.parent.parent if not self.DATA_DIR else Path(self.DATA_DIR)


settings = Settings()
