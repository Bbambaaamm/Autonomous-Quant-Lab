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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
