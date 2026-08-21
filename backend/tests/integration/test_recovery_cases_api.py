"""Integration tests for recovery case APIs."""

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.webhooks.dispatcher import WebhookDispatcher
from tests.fixtures.razorpay import (
    payment_failed_raw_body,
    razorpay_payment_api_response,
    webhook_headers,
)


async def _seed_recovery_case(
    client,
    db_session: AsyncSession,
    settings: Settings,
    *,
    payment_id: str,
    event_id: str,
) -> None:
    """Ingest webhook and run dispatcher to create a recovery case."""
    raw_body = payment_failed_raw_body(payment_id=payment_id)
    headers = webhook_headers(raw_body, event_id=event_id)

    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(
            200,
            json=razorpay_payment_api_response(payment_id=payment_id, status="failed"),
        )
    )

    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )
    assert response.status_code == 200

    dispatcher = WebhookDispatcher(db_session, settings)
    await dispatcher.process_event(event_id)
    await dispatcher.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_recovery_cases(client, db_session, settings):
    """GET /api/v1/recovery-cases returns seeded cases."""
    await _seed_recovery_case(
        client,
        db_session,
        settings,
        payment_id="pay_API_LIST_001",
        event_id="event_api_list_001",
    )

    response = await client.get("/api/v1/recovery-cases")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    assert any(item["payment_id"] == "pay_API_LIST_001" for item in body["items"])


@pytest.mark.asyncio
@respx.mock
async def test_list_recovery_cases_filter_by_status(client, db_session, settings):
    """Status filter returns matching cases only."""
    await _seed_recovery_case(
        client,
        db_session,
        settings,
        payment_id="pay_API_FILTER_001",
        event_id="event_api_filter_001",
    )

    response = await client.get("/api/v1/recovery-cases", params={"status": "DETECTED"})
    assert response.status_code == 200
    assert all(item["status"] == "DETECTED" for item in response.json()["items"])


@pytest.mark.asyncio
@respx.mock
async def test_get_recovery_case_detail(client, db_session, settings):
    """GET /api/v1/recovery-cases/{id} includes audit trail."""
    await _seed_recovery_case(
        client,
        db_session,
        settings,
        payment_id="pay_API_DETAIL_001",
        event_id="event_api_detail_001",
    )

    list_response = await client.get(
        "/api/v1/recovery-cases",
        params={"payment_id": "pay_API_DETAIL_001"},
    )
    case_id = list_response.json()["items"][0]["id"]

    detail_response = await client.get(f"/api/v1/recovery-cases/{case_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["payment_id"] == "pay_API_DETAIL_001"
    assert detail["status"] == "DETECTED"
    assert len(detail["audit_trail"]) >= 1
    assert detail["audit_trail"][0]["to_state"] == "DETECTED"


@pytest.mark.asyncio
async def test_get_recovery_case_not_found(client):
    """Unknown case ID returns 404."""
    response = await client.get(
        "/api/v1/recovery-cases/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 404
