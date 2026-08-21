"""Minimal Razorpay REST API client."""

from typing import Any

import httpx

from app.core.config import Settings


class RazorpayAPIError(Exception):
    """Raised when Razorpay API returns an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Razorpay API error {status_code}: {message}")


class RazorpayClient:
    """Async HTTP client for Razorpay REST endpoints."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.razorpay_api_base_url.rstrip("/"),
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            timeout=settings.razorpay_api_timeout_seconds,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client if owned."""
        if self._owns_client:
            await self._client.aclose()

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        """Fetch authoritative payment status from Razorpay."""
        try:
            response = await self._client.get(f"/v1/payments/{payment_id}")
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RazorpayAPIError(408, "Request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise RazorpayAPIError(
                exc.response.status_code,
                exc.response.text,
            ) from exc

        return response.json()
