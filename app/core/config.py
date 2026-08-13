from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Insecure development defaults. Fine while DEBUG is on; a hard startup failure
# outside development (see Settings._reject_default_secrets_in_production).
DEFAULT_SECRET_KEY = "dev-secret-key-change-in-production"
DEFAULT_ADMIN_API_KEY = "dev-admin-key-change-in-production"
DEFAULT_WEBHOOK_SECRET = "dev-webhook-secret-change-in-production"


class Settings(BaseSettings):
    """Application configuration from environment variables."""

    # App
    APP_NAME: str = "PayCore"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+psycopg://paycore:paycore@localhost:5432/paycore"

    # Security
    SECRET_KEY: str = DEFAULT_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Admin (service-to-service key for internal/ops endpoints like reconciliation)
    ADMIN_API_KEY: str = DEFAULT_ADMIN_API_KEY

    # PIX webhook simulation (server-to-server callback from the PSP)
    WEBHOOK_SECRET: str = DEFAULT_WEBHOOK_SECRET

    # Fraud screening thresholds (all monetary values in cents)
    FRAUD_REVIEW_AMOUNT_CENTS: int = 500_000  # R$ 5.000,00 -> hold for manual review
    FRAUD_BLOCK_AMOUNT_CENTS: int = 5_000_000  # R$ 50.000,00 -> hard block
    FRAUD_VELOCITY_WINDOW_SECONDS: int = 60
    FRAUD_VELOCITY_MAX_DEBITS: int = 5  # reaching this many debits in the window -> review
    FRAUD_DAILY_DEBIT_LIMIT_CENTS: int = 10_000_000  # R$ 100.000,00 in 24h -> block

    # API
    API_V1_STR: str = "/api/v1"

    model_config = {"env_file": ".env", "case_sensitive": True}

    @model_validator(mode="after")
    def _reject_default_secrets_in_production(self) -> "Settings":
        """Refuse to boot with the shipped placeholder secrets when DEBUG is off.

        The insecure defaults exist only for local development. Reaching production
        (DEBUG=false) with them still set would leave JWT signing and the admin key
        trivially forgeable, so we fail fast at startup instead of running exposed.
        """
        if self.DEBUG:
            return self
        insecure = []
        if self.SECRET_KEY == DEFAULT_SECRET_KEY:
            insecure.append("SECRET_KEY")
        if self.ADMIN_API_KEY == DEFAULT_ADMIN_API_KEY:
            insecure.append("ADMIN_API_KEY")
        if self.WEBHOOK_SECRET == DEFAULT_WEBHOOK_SECRET:
            insecure.append("WEBHOOK_SECRET")
        if insecure:
            raise ValueError(
                "Refusing to start with insecure default secrets while DEBUG=false: "
                f"{', '.join(insecure)}. Set secure value(s) via environment before deploying."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
