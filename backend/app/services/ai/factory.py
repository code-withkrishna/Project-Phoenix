"""AI Provider factory."""

from app.core.config import Settings, get_settings
from app.services.ai.base import AIProvider
from app.services.ai.mock import MockAIProvider
from app.services.ai.openai_provider import OpenAICompatibleProvider


def get_ai_provider(settings: Settings | None = None) -> AIProvider:
    """Return configured AIProvider instance based on application settings."""
    cfg = settings or get_settings()

    provider_type = cfg.ai_provider.lower().strip()

    if provider_type in ["openai", "openai-compatible", "production"]:
        return OpenAICompatibleProvider(
            api_key=cfg.ai_api_key,
            base_url=cfg.ai_api_base_url,
            model=cfg.ai_model,
            timeout_seconds=cfg.ai_timeout_seconds,
        )

    # Default to deterministic mock provider for safety & tests
    return MockAIProvider(model_name=cfg.ai_model or "mock-ai-v1")
