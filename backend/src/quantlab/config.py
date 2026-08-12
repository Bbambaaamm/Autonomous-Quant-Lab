from decimal import Decimal
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./quantlab.db"
    app_env: str = "development"
    log_level: str = "INFO"
    automation_enabled: bool = False
    worker_poll_interval: float = 5.0
    worker_lease_timeout: float = 60.0
    worker_heartbeat_interval: float = 15.0
    job_max_attempts: int = 5
    retry_base_delay: float = 5.0
    retry_max_delay: float = 300.0
    worker_batch_size: int = 1
    market_data_provider: str = "stooq"
    market_data_timeout: float = 10.0
    market_data_max_attempts: int = 3
    market_data_sync_overlap: int = 5
    market_data_calendar: str = "XNYS"
    market_data_minimum_coverage: Decimal = Decimal("0.98")
    market_data_staleness_policy: int = 1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_automation(self) -> "Settings":
        if self.worker_poll_interval <= 0 or self.worker_batch_size < 1:
            raise ValueError("Worker polling a batch size musí být kladné")
        if not 0 < self.worker_heartbeat_interval < self.worker_lease_timeout:
            raise ValueError("Heartbeat musí být kladný a kratší než lease timeout")
        if self.job_max_attempts < 1 or self.job_max_attempts > 100:
            raise ValueError("Počet pokusů musí být v rozsahu 1 až 100")
        if self.retry_base_delay <= 0 or self.retry_max_delay < self.retry_base_delay:
            raise ValueError("Retry intervaly nejsou platné")
        if self.market_data_provider not in {"stooq"} or self.market_data_calendar != "XNYS":
            raise ValueError("Market-data provider nebo kalendář není na allowlistu")
        if self.market_data_timeout <= 0 or not 1 <= self.market_data_max_attempts <= 10:
            raise ValueError("Market-data retry konfigurace není platná")
        if self.market_data_sync_overlap < 0 or self.market_data_staleness_policy < 0:
            raise ValueError("Market-data overlap a staleness musí být nezáporné")
        if not Decimal("0") < self.market_data_minimum_coverage <= Decimal("1"):
            raise ValueError("Minimální coverage musí být v intervalu (0, 1]")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
