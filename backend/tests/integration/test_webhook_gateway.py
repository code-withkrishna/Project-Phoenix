"""Integration tests for Razorpay webhook gateway."""

import asyncio
import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.recovery_case import RecoveryCase
from app.models.webhook_event import RawWebhookEvent
from app.services.webhooks.dispatcher import WebhookDispatcher
from tests.fixtures.razorpay import (
    TEST_WEBHOOK_SECRET,
    compute_signature,
    order_paid_payload,
    payment_failed_raw_body,
    razorpay_payment_api_response,
    webhook_headers,
)


@pytest.mark.asyncio
@respx.mock
async def test_valid_payment_failed_webhook(client, db_session):
    """Valid signed payment.failed webhook is ingested and acknowledged."""
    respx.get("https://api.razorpay.com/v1/payments/pay_TEST_FIXTURE_001").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="failed"))
    )
    raw_body = payment_failed_raw_body()
    headers = webhook_headers(raw_body, event_id="event_integ_001")

    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "acknowledged"
    assert body["event_id"] == "event_integ_001"
    assert body["event_type"] == "payment.failed"
    assert body["action_taken"] == "INGESTED"

    result = await db_session.execute(
        select(RawWebhookEvent).where(RawWebhookEvent.event_id == "event_integ_001")
    )
    stored = result.scalar_one()
    assert stored.payload["event"] == "payment.failed"
    assert stored.signature == headers["X-Razorpay-Signature"]


@pytest.mark.asyncio
async def test_invalid_signature_rejected(client):
    """Invalid signature returns 401."""
    raw_body = payment_failed_raw_body()
    headers = webhook_headers(raw_body, event_id="event_integ_bad_sig")
    headers["X-Razorpay-Signature"] = "invalid"

    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_rejected(client):
    """Missing signature returns 401."""
    raw_body = payment_failed_raw_body()
    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Event-Id": "event_integ_no_sig",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
@respx.mock
async def test_duplicate_webhook_deduplicated(client):
    """Duplicate event_id returns DEDUPLICATED."""
    respx.get("https://api.razorpay.com/v1/payments/pay_TEST_FIXTURE_001").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="failed"))
    )
    raw_body = payment_failed_raw_body()
    headers = webhook_headers(raw_body, event_id="event_integ_dup")

    first = await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    second = await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    assert first.status_code == 200
    assert first.json()["action_taken"] == "INGESTED"
    assert second.status_code == 200
    assert second.json()["action_taken"] == "DEDUPLICATED"


@pytest.mark.asyncio
async def test_malformed_json_rejected(client):
    """Malformed JSON body returns 400."""
    raw_body = b"{not-json"
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": "event_integ_bad_json",
        "X-Razorpay-Signature": compute_signature(raw_body),
    }

    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_missing_event_id_rejected(client):
    """Missing X-Razorpay-Event-Id returns 400."""
    raw_body = payment_failed_raw_body()
    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw_body),
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_unsupported_event_acknowledged(client, db_session, settings):
    """Unsupported events are stored and acknowledged without case creation."""
    payload = order_paid_payload(event_id="event_integ_unsupported")
    event_id = payload.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id)

    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["action_taken"] == "INGESTED"

    dispatcher = WebhookDispatcher(db_session, settings)
    await dispatcher.process_event(event_id)
    await dispatcher.close()

    result = await db_session.execute(select(RecoveryCase))
    assert len(result.scalars().all()) == 0


@pytest.mark.asyncio
@respx.mock
async def test_raw_payload_preserved(client, db_session):
    """Stored payload matches the original raw webhook bytes."""
    respx.get("https://api.razorpay.com/v1/payments/pay_RAW_PRESERVE").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(
            payment_id="pay_RAW_PRESERVE", status="failed",
        ))
    )
    raw_body = payment_failed_raw_body(payment_id="pay_RAW_PRESERVE")
    headers = webhook_headers(raw_body, event_id="event_integ_raw")

    await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    result = await db_session.execute(
        select(RawWebhookEvent).where(RawWebhookEvent.event_id == "event_integ_raw")
    )
    stored = result.scalar_one()
    assert json.dumps(stored.payload, separators=(",", ":")).encode("utf-8") == raw_body


@pytest.mark.asyncio
@respx.mock
async def test_end_to_end_creates_recovery_case(client, db_session, settings):
    """Background processing creates recovery case for failed payment."""
    payment_id = "pay_E2E_001"
    raw_body = payment_failed_raw_body(payment_id=payment_id)
    event_id = "event_integ_e2e"
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

    result = await db_session.execute(
        select(RecoveryCase).where(RecoveryCase.payment_id == payment_id)
    )
    case = result.scalar_one_or_none()
    assert case is not None
    assert case.status == "DETECTED"


@pytest.mark.asyncio
@respx.mock
async def test_duplicate_webhook_flood(client, db_session):
    """Concurrent duplicate deliveries create exactly one stored event."""
    respx.get("https://api.razorpay.com/v1/payments/pay_TEST_FIXTURE_001").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="failed"))
    )
    raw_body = payment_failed_raw_body()
    headers = webhook_headers(raw_body, event_id="event_integ_flood")

    tasks = [
        client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
        for _ in range(5)
    ]
    responses = await asyncio.gather(*tasks)

    for response in responses:
        assert response.status_code == 200

    result = await db_session.execute(
        select(RawWebhookEvent).where(RawWebhookEvent.event_id == "event_integ_flood")
    )
    events = result.scalars().all()
    assert len(events) == 1

    ingested = sum(1 for r in responses if r.json()["action_taken"] == "INGESTED")
    deduplicated = sum(1 for r in responses if r.json()["action_taken"] == "DEDUPLICATED")
    assert ingested == 1
    assert deduplicated == 4
