"""Durable webhook processing lease tests."""

import pytest

from app.repositories.webhook_events import WebhookEventRepository


@pytest.mark.asyncio
async def test_webhook_processing_claim_is_single_winner(db_session):
    repo = WebhookEventRepository(db_session)
    await repo.insert_if_new(
        event_id="evt_lease_001",
        event_type="payment.failed",
        entity_id="pay_lease_001",
        payload={"event": "payment.failed"},
        signature="sig",
    )

    first = await repo.claim_for_processing("evt_lease_001")
    second = await repo.claim_for_processing("evt_lease_001")

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_failed_webhook_processing_releases_lease_for_retry(db_session):
    repo = WebhookEventRepository(db_session)
    await repo.insert_if_new(
        event_id="evt_lease_002",
        event_type="payment.failed",
        entity_id="pay_lease_002",
        payload={"event": "payment.failed"},
        signature="sig",
    )

    assert await repo.claim_for_processing("evt_lease_002") is True
    await repo.mark_processing_failed("evt_lease_002", "temporary gateway failure")

    assert await repo.claim_for_processing("evt_lease_002") is True
