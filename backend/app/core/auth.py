"""Authentication dependencies for merchant-facing APIs."""

import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings, get_settings


async def require_merchant_api_key(
    request: Request,
    x_phoenix_api_key: str | None = Header(default=None, alias="X-Phoenix-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Require API-key authentication unless an explicit loopback-only dev bypass is enabled."""
    if (
        settings.phoenix_insecure_dev
        and settings.environment in {"development", "test"}
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
        and not settings.merchant_api_key
    ):
        return

    if not settings.merchant_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Merchant API authentication is not configured",
        )

    if not x_phoenix_api_key or not hmac.compare_digest(x_phoenix_api_key, settings.merchant_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing merchant API key",
        )
