"""Post-ingestion webhook processing orchestration."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.services.razorpay.client import RazorpayClient
from app.services.razorpay.reconciliation import PaymentReconciliationService
from app.services.recovery.case_service import RecoveryCaseService
from app.services.webhooks.ingestion import WebhookIngestionService
from app.services.webhooks.normalization import normalize_payment_failed

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = frozenset({"payment.failed"})


class WebhookDispatcher:
    """Orchestrate background processing for ingested webhook events."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        razorpay_client: RazorpayClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._webhook_repo = WebhookEventRepository(session)
        self._case_repo = RecoveryCaseRepository(session)
        self._owns_client = razorpay_client is None
        self._razorpay_client = razorpay_client or RazorpayClient(settings)
        self._reconciliation = PaymentReconciliationService(self._razorpay_client)
        self._recovery_service = RecoveryCaseService(session, self._reconciliation)

    async def close(self) -> None:
        """Close owned HTTP resources."""
        if self._owns_client:
            await self._razorpay_client.close()

    async def process_event(self, event_id: str) -> None:
        """Process a persisted webhook event by ID."""
        event = await self._webhook_repo.get_by_event_id(event_id)
        if event is None:
            logger.warning("Webhook event not found for processing: event_id=%s", event_id)
            return

        if event.is_processed:
            logger.info("Webhook already processed: event_id=%s", event_id)
            return

        if event.event_type not in SUPPORTED_EVENTS:
            logger.info(
                "Unsupported webhook event acknowledged without processing: event_id=%s type=%s",
                event_id,
                event.event_type,
            )
            await self._webhook_repo.mark_processed(event_id)
            return

        if event.event_type == "payment.failed":
            normalized = normalize_payment_failed(
                event_id=event.event_id,
                payload=event.payload,
            )
            await self._recovery_service.handle_payment_failed(normalized)

        await self._webhook_repo.mark_processed(event_id)


async def process_webhook_event_background(event_id: str, database_url: str) -> None:
    """Background task entrypoint with its own DB session."""
    from app.core.config import get_settings
    from app.core.database import get_session_factory, init_db

    settings = get_settings()
    init_db(database_url)
    session_factory = get_session_factory()

    async with session_factory() as session:
        dispatcher = WebhookDispatcher(session, settings)
        try:
            await dispatcher.process_event(event_id)
        finally:
            await dispatcher.close()
