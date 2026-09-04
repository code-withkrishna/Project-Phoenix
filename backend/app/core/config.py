"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration for the Phoenix backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="phoenix-backend", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/phoenix",
        alias="DATABASE_URL",
    )
    razorpay_key_id: str = Field(default="", alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str = Field(default="", alias="RAZORPAY_KEY_SECRET")
    razorpay_webhook_secret: str = Field(default="", alias="RAZORPAY_WEBHOOK_SECRET")
    razorpay_api_base_url: str = Field(
        default="https://api.razorpay.com",
        alias="RAZORPAY_API_BASE_URL",
    )
    razorpay_api_timeout_seconds: float = Field(
        default=10.0,
        alias="RAZORPAY_API_TIMEOUT_SECONDS",
    )
    ai_provider: str = Field(default="mock", alias="AI_PROVIDER")
    ai_model: str = Field(default="gpt-4o-mini", alias="AI_MODEL")
    ai_api_key: str = Field(default="", alias="AI_API_KEY")
    ai_api_base_url: str = Field(
        default="https://api.openai.com/v1",
        alias="AI_API_BASE_URL",
    )
    ai_timeout_seconds: float = Field(default=15.0, alias="AI_TIMEOUT_SECONDS")
    auto_orchestrate: bool = Field(default=True, alias="AUTO_ORCHESTRATE")



@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
