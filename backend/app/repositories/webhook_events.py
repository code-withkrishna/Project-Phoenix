"""Webhook event repository."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_event import RawWebhookEvent


class WebhookEventRepository:
    """Data access for raw webhook events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(
        self,
        *,
        event_id: str,
        event_type: str,
        entity_id: str,
        payload: dict,
        signature: str,
    ) -> tuple[RawWebhookEvent | None, bool]:
        """Insert event idempotently. Returns (event, created)."""
        stmt = (
            insert(RawWebhookEvent)
            .values(
                id=uuid.uuid4(),
                event_id=event_id,
                event_type=event_type,
                entity_id=entity_id,
                payload=payload,
                signature=signature,
                is_processed=False,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(RawWebhookEvent)
        )
        result = await self._session.execute(stmt)
        created = result.scalar_one_or_none()
        if created is not None:
            await self._session.commit()
            return created, True

        existing = await self.get_by_event_id(event_id)
        return existing, False

    async def get_by_event_id(self, event_id: str) -> RawWebhookEvent | None:
        """Fetch a webhook event by Razorpay event ID."""
        result = await self._session.execute(
            select(RawWebhookEvent).where(RawWebhookEvent.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def claim_for_processing(
        self,
        event_id: str,
        *,
        lease_seconds: int = 300,
    ) -> bool:
        """Atomically claim an unprocessed event for one worker."""
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=lease_seconds)
        stmt = (
            update(RawWebhookEvent)
            .where(
                RawWebhookEvent.event_id == event_id,
                RawWebhookEvent.is_processed.is_(False),
                or_(
                    RawWebhookEvent.processing_started_at.is_(None),
                    RawWebhookEvent.processing_started_at < stale_before,
                ),
            )
            .values(
                processing_started_at=now,
                processing_attempts=RawWebhookEvent.processing_attempts + 1,
                last_processing_error=None,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount == 1

    async def mark_processed(self, event_id: str) -> None:
        """Mark a webhook event as processed and release its lease."""
        event = await self.get_by_event_id(event_id)
        if event is None:
            return
        event.is_processed = True
        event.processing_started_at = None
        event.last_processing_error = None
        await self._session.commit()

    async def mark_processing_failed(self, event_id: str, error: str) -> None:
        """Release a processing lease while retaining a bounded retry error."""
        event = await self.get_by_event_id(event_id)
        if event is None or event.is_processed:
            return
        event.processing_started_at = None
        event.last_processing_error = error[:1024]
        await self._session.commit()

    async def list_pending_event_ids(self, limit: int = 25) -> list[str]:
        """Return persisted events that are ready for processing or retry."""
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=300)
        result = await self._session.execute(
            select(RawWebhookEvent.event_id)
            .where(
                RawWebhookEvent.is_processed.is_(False),
                or_(
                    RawWebhookEvent.processing_started_at.is_(None),
                    RawWebhookEvent.processing_started_at < stale_before,
                ),
            )
            .order_by(RawWebhookEvent.received_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
