import pytest
from pydantic import ValidationError

from app.core.config import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_SECRET_KEY,
    DEFAULT_WEBHOOK_SECRET,
    Settings,
)

# _env_file=None keeps these unit tests from picking up a developer's local .env,
# so behavior is identical in CI (no .env) and on a workstation (DEBUG=true).


def test_production_rejects_default_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            DEBUG=False,
            SECRET_KEY=DEFAULT_SECRET_KEY,
            ADMIN_API_KEY="a-secure-admin-key",
            WEBHOOK_SECRET="a-secure-webhook-secret",
            _env_file=None,
        )


def test_production_rejects_default_webhook_secret():
    with pytest.raises(ValidationError, match="WEBHOOK_SECRET"):
        Settings(
            DEBUG=False,
            SECRET_KEY="a-secure-secret-key",
            ADMIN_API_KEY="a-secure-admin-key",
            WEBHOOK_SECRET=DEFAULT_WEBHOOK_SECRET,
            _env_file=None,
        )


def test_production_rejects_default_admin_key():
    with pytest.raises(ValidationError, match="ADMIN_API_KEY"):
        Settings(
            DEBUG=False,
            SECRET_KEY="a-secure-secret-key",
            ADMIN_API_KEY=DEFAULT_ADMIN_API_KEY,
            WEBHOOK_SECRET="a-secure-webhook-secret",
            _env_file=None,
        )


def test_production_reports_every_insecure_default_at_once():
    with pytest.raises(ValidationError, match="SECRET_KEY, ADMIN_API_KEY, WEBHOOK_SECRET"):
        Settings(
            DEBUG=False,
            SECRET_KEY=DEFAULT_SECRET_KEY,
            ADMIN_API_KEY=DEFAULT_ADMIN_API_KEY,
            WEBHOOK_SECRET=DEFAULT_WEBHOOK_SECRET,
            _env_file=None,
        )


def test_production_boots_with_secure_secrets():
    settings = Settings(
        DEBUG=False,
        SECRET_KEY="a-secure-secret-key",
        ADMIN_API_KEY="a-secure-admin-key",
        WEBHOOK_SECRET="a-secure-webhook-secret",
        _env_file=None,
    )
    assert settings.DEBUG is False


def test_development_tolerates_default_secrets():
    """With DEBUG on, the placeholder secrets are allowed (local convenience)."""
    settings = Settings(
        DEBUG=True,
        SECRET_KEY=DEFAULT_SECRET_KEY,
        ADMIN_API_KEY=DEFAULT_ADMIN_API_KEY,
        WEBHOOK_SECRET=DEFAULT_WEBHOOK_SECRET,
        _env_file=None,
    )
    assert settings.SECRET_KEY == DEFAULT_SECRET_KEY
