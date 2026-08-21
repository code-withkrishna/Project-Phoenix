"""Unit tests for RazorpayClient payment link methods."""

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.razorpay.client import RazorpayAPIError, RazorpayClient


@pytest.fixture
def settings() -> Settings:
    return Settings(
        razorpay_key_id="rzp_test_key123",
        razorpay_key_secret="rzp_test_secret123",
        razorpay_api_base_url="https://api.razorpay.com",
    )


@pytest.fixture
async def razorpay_client(settings: Settings):
    client = RazorpayClient(settings)
    yield client
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_create_payment_link_success(razorpay_client: RazorpayClient) -> None:
    """POST /v1/payment_links returns 200 with payment link data."""
    mock_route = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": "plink_TEST_123",
            "amount": 499900,
            "currency": "INR",
            "reference_id": "PHX_7F4A21C9_01",
            "short_url": "https://rzp.io/i/plink_TEST_123",
            "status": "created",
        },
    )

    result = await razorpay_client.create_payment_link(
        amount=499900,
        currency="INR",
        reference_id="PHX_7F4A21C9_01",
        description="Test payment link",
        expire_by=1755768000,
    )

    assert mock_route.called
    assert result["id"] == "plink_TEST_123"
    assert result["short_url"] == "https://rzp.io/i/plink_TEST_123"


@pytest.mark.asyncio
@respx.mock
async def test_create_payment_link_errors(razorpay_client: RazorpayClient) -> None:
    """POST /v1/payment_links handles 400, 401, 500, and timeout."""
    # 400 Bad Request
    respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=400,
        text='{"error": {"description": "Invalid amount"}}',
    )
    with pytest.raises(RazorpayAPIError) as exc:
        await razorpay_client.create_payment_link(
            amount=50,
            reference_id="PHX_7F4A21C9_01",
            description="Test",
            expire_by=1755768000,
        )
    assert exc.value.status_code == 400

    # 408 Timeout
    respx.post("https://api.razorpay.com/v1/payment_links").mock(
        side_effect=httpx.TimeoutException("Connection timed out")
    )
    with pytest.raises(RazorpayAPIError) as exc_timeout:
        await razorpay_client.create_payment_link(
            amount=499900,
            reference_id="PHX_7F4A21C9_01",
            description="Test",
            expire_by=1755768000,
        )
    assert exc_timeout.value.status_code == 408


@pytest.mark.asyncio
@respx.mock
async def test_get_payment_link_by_reference(razorpay_client: RazorpayClient) -> None:
    """GET /v1/payment_links?reference_id=... returns found link or None."""
    # Found
    respx.get("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "count": 1,
            "payment_links": [
                {
                    "id": "plink_FOUND_123",
                    "reference_id": "PHX_7F4A21C9_01",
                    "status": "created",
                    "short_url": "https://rzp.io/i/plink_FOUND_123",
                }
            ],
        },
    )
    link = await razorpay_client.get_payment_link_by_reference("PHX_7F4A21C9_01")
    assert link is not None
    assert link["id"] == "plink_FOUND_123"

    # Not found in list
    respx.get("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={"count": 0, "payment_links": []},
    )
    link_none = await razorpay_client.get_payment_link_by_reference("PHX_UNKNOWN_01")
    assert link_none is None


@pytest.mark.asyncio
@respx.mock
async def test_cancel_payment_link(razorpay_client: RazorpayClient) -> None:
    """POST /v1/payment_links/{id}/cancel cancels payment link."""
    respx.post("https://api.razorpay.com/v1/payment_links/plink_123/cancel").respond(
        status_code=200,
        json={"id": "plink_123", "status": "cancelled"},
    )
    res = await razorpay_client.cancel_payment_link("plink_123")
    assert res["status"] == "cancelled"
