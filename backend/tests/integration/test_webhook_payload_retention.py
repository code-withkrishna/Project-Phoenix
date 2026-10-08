"""Webhook payload retention tests."""

import pytest

from app.repositories.webhook_events import WebhookEventRepository


@pytest.mark.asyncio
async def test_processed_webhook_payload_is_redacted_and_fingerprinted(db_session):
    repo = WebhookEventRepository(db_session)
    original = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_private_001",
                    "email": "customer@example.com",
                    "contact": "+919999999999",
                }
            }
        },
    }
    await repo.insert_if_new(
        event_id="evt_retention_001",
        event_type="payment.failed",
        entity_id="pay_private_001",
        payload=original,
        signature="sig",
    )

    await repo.claim_for_processing("evt_retention_001")
    await repo.mark_processed("evt_retention_001")

    event = await repo.get_by_event_id("evt_retention_001")
    assert event is not None
    assert event.is_processed is True
    assert event.payload == {
        "event": "payment.failed",
        "entity_id": "pay_private_001",
        "redacted": True,
    }
    assert event.payload_sha256 is not None
    assert len(event.payload_sha256) == 64
    assert event.payload_redacted_at is not None
