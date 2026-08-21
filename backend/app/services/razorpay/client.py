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

    async def create_payment_link(
        self,
        *,
        amount: int,
        currency: str = "INR",
        reference_id: str,
        description: str,
        expire_by: int,
        customer: dict[str, Any] | None = None,
        notify: dict[str, bool] | None = None,
        notes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new standard Payment Link via Razorpay API."""
        payload: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "accept_partial": False,
            "reference_id": reference_id,
            "description": description or "Complete your order with instant 1-click payment.",
            "expire_by": expire_by,
            "notify": notify if notify is not None else {"sms": True, "email": True},
        }
        if customer:
            payload["customer"] = {k: v for k, v in customer.items() if v is not None}
        if notes:
            payload["notes"] = notes

        try:
            response = await self._client.post("/v1/payment_links", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RazorpayAPIError(408, "Request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise RazorpayAPIError(
                exc.response.status_code,
                exc.response.text,
            ) from exc

        return response.json()

    async def get_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        """Fetch a payment link by its ID."""
        try:
            response = await self._client.get(f"/v1/payment_links/{payment_link_id}")
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RazorpayAPIError(408, "Request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise RazorpayAPIError(
                exc.response.status_code,
                exc.response.text,
            ) from exc

        return response.json()

    async def get_payment_link_by_reference(self, reference_id: str) -> dict[str, Any] | None:
        """Query Razorpay for an existing payment link with the given reference_id."""
        try:
            response = await self._client.get("/v1/payment_links", params={"reference_id": reference_id})
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            # Razorpay returns {"payment_links": [...], "count": ...} or list of items
            links = data.get("payment_links", []) if isinstance(data, dict) else data
            for link in links:
                if link.get("reference_id") == reference_id:
                    return link
            return None
        except httpx.TimeoutException as exc:
            raise RazorpayAPIError(408, "Request timed out") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise RazorpayAPIError(
                exc.response.status_code,
                exc.response.text,
            ) from exc

    async def cancel_payment_link(self, payment_link_id: str) -> dict[str, Any]:
        """Cancel an active payment link."""
        try:
            response = await self._client.post(f"/v1/payment_links/{payment_link_id}/cancel")
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RazorpayAPIError(408, "Request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise RazorpayAPIError(
                exc.response.status_code,
                exc.response.text,
            ) from exc

        return response.json()

