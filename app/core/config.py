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
    # 대상 종목 코드 (쉼표 구분, 예: "005930,000660")
    TARGET_SYMBOLS: str = "005930,000660"

    # 데이터/상태 파일 기준 디렉터리 (비우면 프로젝트 루트)
    DATA_DIR: str = ""

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def target_symbols_list(self) -> list[str]:
        return _parse_symbols(self.TARGET_SYMBOLS)

    @property
    def base_dir(self) -> Path:
        """상태/토큰 파일을 둘 디렉터리 (프로젝트 루트 기준)"""
        return Path(__file__).resolve().parent.parent.parent if not self.DATA_DIR else Path(self.DATA_DIR)


settings = Settings()
