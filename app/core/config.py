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
    ENTRY_NO_AFTER_HOUR: int = 14      # 이 시각 이후에는 신규 매수 금지 (14 = 14:00, 14:30은 14+30/60으로 별도)
    ENTRY_NO_AFTER_MINUTE: int = 30    # 14:30 = ENTRY_NO_AFTER_HOUR 14, ENTRY_NO_AFTER_MINUTE 30
    ENTRY_GAP_UP_PCT: float = 5.0      # 당일 시가가 전일 종가 대비 이 비율 이상 갭업이면 진입 스킵 (0=미적용)
    ENTRY_VOLUME_RATIO: float = 1.5    # 돌파 시점 거래량 >= 직전 20봉 평균 * 이 값일 때만 진입 (0=미적용)

    # 일별 리스크 관리
    DAILY_LOSS_LIMIT_PCT: float = -2.0   # 당일 실현손실이 총자산 대비 이 % 이하면 신규 매수 중단
    MAX_CONSECUTIVE_LOSSES: int = 3      # 이 횟수 연패 시 다음 매수 예산 축소
    BUDGET_CUT_ON_STREAK: float = 0.5    # 연패 시 적용할 예산 비율 (0.5 = 50%)
    MAX_DAILY_TRADES: int = 6            # 당일 체결 건수(BUY+SELL) 이하면 신규 매수 허용 (초과 시 신규 매수만 중단)

    # ATR 손절 (변동성 돌파 전략)
    USE_ATR_STOP: bool = False          # True 시 ATR 기반 손절, False 시 기존 trailing_stop_pct
    ATR_PERIOD: int = 20
    ATR_MULTIPLIER: float = 1.5

    # 종목 스코어링 (동적 종목 사용 시 상위 N개만 진입)
    USE_STOCK_SCORING: bool = False      # True 시 후보를 스코어로 정렬 후 상위 MAX_SLOTS만

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
