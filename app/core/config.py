from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration from environment variables."""

    # App
    APP_NAME: str = "PayCore"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+psycopg://paycore:paycore@localhost:5432/paycore"

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # Admin (service-to-service key for internal/ops endpoints like reconciliation)
    ADMIN_API_KEY: str = "dev-admin-key-change-in-production"

    # Fraud screening thresholds (all monetary values in cents)
    FRAUD_REVIEW_AMOUNT_CENTS: int = 500_000  # R$ 5.000,00 -> hold for manual review
    FRAUD_BLOCK_AMOUNT_CENTS: int = 5_000_000  # R$ 50.000,00 -> hard block
    FRAUD_VELOCITY_WINDOW_SECONDS: int = 60
    FRAUD_VELOCITY_MAX_DEBITS: int = 5  # reaching this many debits in the window -> review
    FRAUD_DAILY_DEBIT_LIMIT_CENTS: int = 10_000_000  # R$ 100.000,00 in 24h -> block

    # API
    API_V1_STR: str = "/api/v1"

    model_config = {"env_file": ".env", "case_sensitive": True}


@lru_cache
def get_settings() -> Settings:
    return Settings()
