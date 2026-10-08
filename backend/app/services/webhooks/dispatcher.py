"""Post-ingestion webhook processing orchestration."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.repositories.webhook_events import WebhookEventRepository
from app.services.ai.base import AIProvider
from app.services.ai.factory import get_ai_provider
from app.services.policy.engine import PolicyEngine
from app.services.policy.models import MerchantPolicy
from app.services.razorpay.client import RazorpayClient
from app.services.razorpay.reconciliation import PaymentReconciliationService
from app.services.recovery.case_service import RecoveryCaseService
from app.services.recovery.execution_guard import ExecutionGuard
from app.services.recovery.orchestrator import RecoveryOrchestrator
from app.services.webhooks.normalization import normalize_payment_failed, normalize_payment_link_event

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = frozenset({
    "payment.failed",
    "payment_link.paid",
    "payment_link.expired",
    "payment_link.cancelled",
    "payment_link.partially_paid",
})


class WebhookDispatcher:
    """Orchestrate durable processing for ingested webhook events."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        razorpay_client: RazorpayClient | None = None,
        reconciliation_service: PaymentReconciliationService | None = None,
        ai_provider: AIProvider | None = None,
        policy_engine: PolicyEngine | None = None,
        execution_guard: ExecutionGuard | None = None,
        merchant_policy: MerchantPolicy | None = None,
        auto_orchestrate: bool | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._webhook_repo = WebhookEventRepository(session)
        self._case_repo = RecoveryCaseRepository(session)
        self._owns_client = razorpay_client is None
        self._razorpay_client = razorpay_client or RazorpayClient(settings)
        self._reconciliation = reconciliation_service or PaymentReconciliationService(self._razorpay_client)
        self._auto_orchestrate = auto_orchestrate if auto_orchestrate is not None else getattr(settings, "auto_orchestrate", True)
        self._orchestrator = RecoveryOrchestrator(
            session=session,
            razorpay_client=self._razorpay_client,
            ai_provider=ai_provider or get_ai_provider(settings),
            policy_engine=policy_engine,
            execution_guard=execution_guard,
        )
        self._recovery_service = RecoveryCaseService(
            session,
            self._reconciliation,
            orchestrator=self._orchestrator if self._auto_orchestrate else None,
            merchant_policy=merchant_policy,
        )

    async def close(self) -> None:
        """Close owned HTTP resources."""
        if self._owns_client:
            await self._razorpay_client.close()

    async def process_event(self, event_id: str) -> None:
        """Process a persisted webhook event by ID with a DB-backed lease."""
        event = await self._webhook_repo.get_by_event_id(event_id)
        if event is None or event.is_processed:
            return
        if not await self._webhook_repo.claim_for_processing(event_id):
            logger.info("Webhook processing already claimed: event_id=%s", event_id)
            return
        try:
            await self._process_claimed_event(event)
        except Exception as exc:
            await self._webhook_repo.mark_processing_failed(event_id, str(exc))
            logger.exception("Webhook processing failed; event will be retried: event_id=%s", event_id)
            raise

    async def _process_claimed_event(self, event) -> None:
        event_id = event.event_id
        if event.event_type not in SUPPORTED_EVENTS:
            await self._webhook_repo.mark_processed(event_id)
            return
        if event.event_type == "payment.failed":
            normalized = normalize_payment_failed(event_id=event.event_id, payload=event.payload)
            await self._recovery_service.handle_payment_failed(normalized)
        else:
            normalized_link = normalize_payment_link_event(
                event_id=event.event_id,
                event_type=event.event_type,
                payload=event.payload,
            )
            if event.event_type == "payment_link.paid":
                await self._recovery_service.handle_payment_link_paid(normalized_link)
            elif event.event_type == "payment_link.expired":
                await self._recovery_service.handle_payment_link_expired(normalized_link)
            elif event.event_type == "payment_link.cancelled":
                await self._recovery_service.handle_payment_link_cancelled(normalized_link)
            elif event.event_type == "payment_link.partially_paid":
                await self._recovery_service.handle_payment_link_partially_paid(normalized_link)
        await self._webhook_repo.mark_processed(event_id)


async def run_webhook_worker(settings: Settings, stop_event: asyncio.Event) -> None:
    """Continuously drain durable webhook jobs until shutdown."""
    from app.core.database import get_session_factory, init_db

    init_db(settings.database_url)
    while not stop_event.is_set():
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                repo = WebhookEventRepository(session)
                event_ids = await repo.list_pending_event_ids(settings.webhook_worker_batch_size)
                for event_id in event_ids:
                    dispatcher = WebhookDispatcher(session, settings)
                    try:
                        await dispatcher.process_event(event_id)
                    finally:
                        await dispatcher.close()
            if not event_ids:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=settings.webhook_worker_interval_seconds)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Durable webhook worker iteration failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.webhook_worker_interval_seconds)
            except asyncio.TimeoutError:
                pass
