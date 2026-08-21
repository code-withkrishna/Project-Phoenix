"""Webhook ingestion service."""

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.webhook_events import WebhookEventRepository

logger = logging.getLogger(__name__)


class WebhookIngestionError(Exception):
    """Raised when webhook payload cannot be ingested."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class WebhookIngestionService:
    """Persist inbound webhook events with idempotent deduplication."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = WebhookEventRepository(session)

    @staticmethod
    def parse_payload(raw_body: bytes) -> dict[str, Any]:
        """Parse raw JSON body without mutating bytes used for signature."""
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookIngestionError("Invalid JSON body", status_code=400) from exc

    @staticmethod
    def extract_entity_id(event_type: str, payload: dict[str, Any]) -> str:
        """Derive primary entity ID from webhook payload."""
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        if payment_entity.get("id"):
            return str(payment_entity["id"])

        payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        if payment_link_entity.get("id"):
            return str(payment_link_entity["id"])

        order_entity = payload.get("payload", {}).get("order", {}).get("entity", {})
        if order_entity.get("id"):
            return str(order_entity["id"])

        return event_type

    async def ingest(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        signature: str,
    ) -> tuple[Any, bool]:
        """Persist webhook event. Returns (event, created)."""
        entity_id = self.extract_entity_id(event_type, payload)
        event, created = await self._repo.insert_if_new(
            event_id=event_id,
            event_type=event_type,
            entity_id=entity_id,
            payload=payload,
            signature=signature,
        )
        if created:
            logger.info(
                "Webhook ingested: event_id=%s event_type=%s entity_id=%s",
                event_id,
                event_type,
                entity_id,
            )
        else:
            logger.info("Webhook deduplicated: event_id=%s", event_id)
        return event, created
