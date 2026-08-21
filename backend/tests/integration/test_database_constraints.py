"""Database constraint and idempotency tests."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recovery_cases import RecoveryCaseRepository
from app.repositories.webhook_events import WebhookEventRepository


@pytest.mark.asyncio
async def test_webhook_event_uniqueness(db_session: AsyncSession):
    """Duplicate event_id insert is idempotent."""
    repo = WebhookEventRepository(db_session)

    first, created_first = await repo.insert_if_new(
        event_id="event_db_unique_001",
        event_type="payment.failed",
        entity_id="pay_db_001",
        payload={"event": "payment.failed"},
        signature="sig1",
    )
    second, created_second = await repo.insert_if_new(
        event_id="event_db_unique_001",
        event_type="payment.failed",
        entity_id="pay_db_001",
        payload={"event": "payment.failed"},
        signature="sig2",
    )

    assert created_first is True
    assert created_second is False
    assert first is not None
    assert second is not None
    assert first.id == second.id


@pytest.mark.asyncio
async def test_recovery_case_uniqueness(db_session: AsyncSession):
    """Duplicate payment_id insert is idempotent."""
    repo = RecoveryCaseRepository(db_session)

    first, created_first = await repo.create_if_absent(
        payment_id="pay_db_unique_001",
        order_id="order_001",
        amount=10000,
        currency="INR",
        customer_email="a@example.com",
        customer_phone="+911111111111",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={"status": "failed"},
    )
    second, created_second = await repo.create_if_absent(
        payment_id="pay_db_unique_001",
        order_id="order_001",
        amount=10000,
        currency="INR",
        customer_email="a@example.com",
        customer_phone="+911111111111",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={"status": "failed"},
    )

    assert created_first is True
    assert created_second is False
    assert first is not None
    assert second is not None
    assert first.id == second.id


@pytest.mark.asyncio
async def test_webhook_event_unique_constraint_violation(db_session: AsyncSession):
    """Direct duplicate insert without ON CONFLICT raises IntegrityError."""
    from app.models.webhook_event import RawWebhookEvent

    db_session.add(
        RawWebhookEvent(
            event_id="event_db_constraint",
            event_type="payment.failed",
            entity_id="pay_x",
            payload={"event": "payment.failed"},
            signature="sig",
        )
    )
    await db_session.commit()

    db_session.add(
        RawWebhookEvent(
            event_id="event_db_constraint",
            event_type="payment.failed",
            entity_id="pay_x",
            payload={"event": "payment.failed"},
            signature="sig2",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
