from decimal import Decimal
from functools import lru_cache
from secrets import token_urlsafe

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
    worker_id_prefix: str = "quantlab-worker"
    worker_require_production: bool = False
    market_data_provider: str = "stooq"
    market_data_timeout: float = 10.0
    market_data_max_attempts: int = 3
    market_data_sync_overlap: int = 5
    market_data_calendar: str = "XNYS"
    market_data_minimum_coverage: Decimal = Decimal("0.98")
    market_data_staleness_policy: int = 1
    api_viewer_token: str = ""
    api_operator_token: str = ""
    api_admin_token: str = ""
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    api_read_limit: int = 120
    api_mutation_limit: int = 20
    api_halt_limit: int = 10
    api_resume_limit: int = 5
    api_auth_failure_limit: int = 20

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
        tokens = [self.api_viewer_token, self.api_operator_token, self.api_admin_token]
        if self.app_env == "production":
            if self.database_url.startswith("sqlite"):
                raise ValueError("Production vyžaduje PostgreSQL")
            if "*" in self.allowed_hosts:
                raise ValueError("Production nepovoluje wildcard trusted host")
            if any(not _strong_secret(token) for token in tokens) or len(set(tokens)) != 3:
                raise ValueError("Production API tokeny musí být unikátní a silné")
        elif not any(tokens):
            # Vývoj používá náhodné ephemeral credentials, nikoli secret uložený v repository.
            self.api_viewer_token = token_urlsafe(32)
            self.api_operator_token = token_urlsafe(32)
            self.api_admin_token = token_urlsafe(32)
        elif any(not token for token in tokens) or len(set(tokens)) != 3:
            raise ValueError("Všechny tři API role musí mít unikátní credentials")
        return self

    def validate_worker_runtime(self) -> None:
        """Odmítne neúplnou nebo omylem vypnutou produkční konfiguraci workeru."""
        if self.worker_require_production and self.app_env != "production":
            raise ValueError("Production worker vyžaduje APP_ENV=production")
        if self.app_env == "production" and not self.database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ValueError("Production worker vyžaduje PostgreSQL DATABASE_URL")
        if not self.automation_enabled:
            raise ValueError("Production worker vyžaduje AUTOMATION_ENABLED=true")
        if not self.worker_id_prefix.strip() or len(self.worker_id_prefix) > 64:
            raise ValueError("WORKER_ID_PREFIX musí mít 1 až 64 znaků")

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return tuple(host.strip() for host in self.trusted_hosts.split(",") if host.strip())


def _strong_secret(value: str) -> bool:
    lowered = value.lower()
    return len(value) >= 43 and not any(word in lowered for word in ("changeme", "placeholder"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
