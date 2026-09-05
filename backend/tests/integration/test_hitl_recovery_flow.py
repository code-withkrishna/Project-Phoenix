"""Integration tests for Human-in-the-Loop recovery flow."""

import httpx
import pytest
import respx
from uuid import uuid4

from app.core.config import Settings
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.services.webhooks.dispatcher import WebhookDispatcher
from tests.fixtures.razorpay import (
    payment_failed_raw_body,
    razorpay_payment_api_response,
    webhook_headers,
)


async def _seed_test_case(
    client,
    db_session,
    settings: Settings,
    *,
    payment_id: str,
    event_id: str,
) -> str:
    """Helper to seed a valid recovery case."""
    raw_body = payment_failed_raw_body(payment_id=payment_id)
    headers = webhook_headers(raw_body, event_id=event_id)

    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(
            200,
            json=razorpay_payment_api_response(payment_id=payment_id, status="failed"),
        )
    )

    resp = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )
    assert resp.status_code == 200

    dispatcher = WebhookDispatcher(db_session, settings)
    await dispatcher.process_event(event_id)
    await dispatcher.close()

    repo = RecoveryCaseRepository(db_session)
    case = await repo.get_by_payment_id(payment_id)
    assert case is not None
    return str(case.id)


@pytest.mark.asyncio
@respx.mock
async def test_hitl_manual_approval_flow(client, db_session, settings):
    """Test full HITL approval workflow: case -> approve -> payment link generated."""
    case_id = await _seed_test_case(
        client,
        db_session,
        settings,
        payment_id="pay_hitl_app_01",
        event_id="evt_hitl_app_01",
    )

    mock_link_resp = {
        "id": f"plink_hitl_{uuid4().hex[:8]}",
        "short_url": "https://rzp.io/i/phx_hitl_test",
        "status": "created",
        "amount": 299900,
        "currency": "INR",
        "reference_id": f"PHX_{case_id[:8]}_1",
    }

    respx.post("https://api.razorpay.com/v1/payment_links").mock(
        return_value=httpx.Response(
            200,
            json=mock_link_resp,
        )
    )

    resp = await client.post(
        f"/api/v1/recovery-cases/{case_id}/approve",
        json={"reason": "Approved VIP customer recovery by merchant admin", "link_expiry_minutes": 60},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "AWAITING_PAYMENT"
    assert len(data["recovery_actions"]) > 0


@pytest.mark.asyncio
@respx.mock
async def test_hitl_manual_rejection_flow(client, db_session, settings):
    """Test HITL rejection: case -> reject -> CANCELLED."""
    case_id = await _seed_test_case(
        client,
        db_session,
        settings,
        payment_id="pay_hitl_rej_01",
        event_id="evt_hitl_rej_01",
    )

    resp = await client.post(
        f"/api/v1/recovery-cases/{case_id}/reject",
        json={"reason": "Suspicious activity detected by merchant security"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "CANCELLED"
