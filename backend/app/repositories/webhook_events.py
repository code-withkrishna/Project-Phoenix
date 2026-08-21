"""Webhook event repository."""

import uuid

from sqlalchemy import select
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

    async def mark_processed(self, event_id: str) -> None:
        """Mark a webhook event as processed."""
        event = await self.get_by_event_id(event_id)
        if event is None:
            return
        event.is_processed = True
        await self._session.commit()
