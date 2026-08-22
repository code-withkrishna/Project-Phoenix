"""Unit tests for application settings."""

from app.core.config import Settings, get_settings


def test_settings_defaults(monkeypatch):
    """Default settings include database and Razorpay fields."""
    for key in (
        "DATABASE_URL",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "phoenix-backend"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.log_level == "INFO"
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.razorpay_key_id == ""
    assert settings.razorpay_key_secret == ""
    assert settings.razorpay_webhook_secret == ""


def test_get_settings_is_cached():
    """Settings loader returns a singleton instance."""
    assert get_settings() is get_settings()
