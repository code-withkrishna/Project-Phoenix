"""Authentication dependencies for merchant-facing APIs."""

from fastapi import Header, HTTPException, status

from app.core.config import Settings, get_settings


async def require_merchant_api_key(
    x_phoenix_api_key: str | None = Header(default=None, alias="X-Phoenix-API-Key"),
    settings: Settings = get_settings(),
) -> None:
    """Require a configured merchant API key outside development/test environments."""
    if not settings.merchant_api_key:
        if settings.environment in {"development", "test"}:
            return
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Merchant API authentication is not configured",
        )

    if x_phoenix_api_key != settings.merchant_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing merchant API key",
        )
