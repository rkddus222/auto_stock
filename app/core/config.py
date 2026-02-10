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
