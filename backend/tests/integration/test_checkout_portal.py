"""Integration tests for checkout portal and order creation endpoints."""

import httpx
import pytest
import respx

from app.core.config import Settings


@pytest.mark.asyncio
async def test_checkout_portal_renders_html(client) -> None:
    """GET /api/v1/checkout/portal returns 200 with HTML content."""
    response = await client.get("/api/v1/checkout/portal")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Project Phoenix Autonomous Recovery" in response.text
    assert "/api/v1/recovery-cases/" in response.text
    # Ensure obsolete /actions endpoint is NOT in the frontend JS
    assert "/actions" not in response.text


@pytest.mark.asyncio
@respx.mock
async def test_checkout_create_order(client) -> None:
    """POST /api/v1/checkout/create-order calls Razorpay and returns order details."""
    respx.post("https://api.razorpay.com/v1/orders").respond(
        status_code=200,
        json={
            "id": "order_TEST_CHECKOUT_001",
            "amount": 299900,
            "currency": "INR",
            "receipt": "rcpt_phx_12345678",
            "status": "created",
        },
    )

    response = await client.post(
        "/api/v1/checkout/create-order",
        json={
            "amount": 299900,
            "currency": "INR",
            "customer_name": "Aditya Sharma",
            "customer_email": "aditya.sharma@example.com",
            "customer_phone": "+919876543210",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == "order_TEST_CHECKOUT_001"
    assert data["amount"] == 299900
    assert data["currency"] == "INR"
    assert "key_id" in data
