"""Recovery case lifecycle service."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.webhook import NormalizedPaymentFailedEvent
from app.services.razorpay.reconciliation import PaymentReconciliationService

logger = logging.getLogger(__name__)


class RecoveryCaseService:
    """Create and manage recovery cases after reconciliation."""

    def __init__(
        self,
        session: AsyncSession,
        reconciliation_service: PaymentReconciliationService,
    ) -> None:
        self._repo = RecoveryCaseRepository(session)
        self._reconciliation = reconciliation_service

    async def handle_payment_failed(
        self,
        event: NormalizedPaymentFailedEvent,
    ) -> None:
        """Reconcile payment state and create recovery case when genuinely failed."""
        reconciliation = await self._reconciliation.reconcile_payment(event.payment_id)

        if reconciliation.is_resolved:
            existing = await self._repo.get_by_payment_id(event.payment_id)
            if existing is not None:
                await self._repo.update_status(
                    existing,
                    new_status="RESOLVED_EXTERNALLY",
                    trigger="RECONCILE_CHECK",
                    actor="RECONCILIATION_WORKER",
                    context_metadata={
                        "authoritative_status": reconciliation.authoritative_status,
                        "webhook_event_id": event.event_id,
                    },
                )
            else:
                await self._repo.append_audit(
                    case_id=None,
                    from_state=None,
                    to_state="RESOLVED_EXTERNALLY",
                    trigger="RECONCILE_CHECK",
                    actor="RECONCILIATION_WORKER",
                    context_metadata={
                        "payment_id": event.payment_id,
                        "authoritative_status": reconciliation.authoritative_status,
                        "webhook_event_id": event.event_id,
                        "note": "Payment already captured; no recovery case created",
                    },
                )
            logger.info(
                "Payment resolved externally, skipping recovery case: payment_id=%s",
                event.payment_id,
            )
            return

        if not reconciliation.is_failed:
            logger.warning(
                "Payment in unexpected state after reconciliation: payment_id=%s status=%s",
                event.payment_id,
                reconciliation.authoritative_status,
            )
            return

        case, created = await self._repo.create_if_absent(
            payment_id=event.payment_id,
            order_id=event.order_id,
            amount=event.amount,
            currency=event.currency,
            customer_email=event.customer_email,
            customer_phone=event.customer_phone,
            failure_code=event.failure_code,
            failure_reason=event.failure_reason,
            failure_telemetry=event.failure_telemetry,
            status="DETECTED",
        )

        if case is None:
            logger.error("Failed to create or fetch recovery case: payment_id=%s", event.payment_id)
            return

        if created:
            await self._repo.append_audit(
                case_id=case.id,
                from_state=None,
                to_state="DETECTED",
                trigger="WEBHOOK_PAYMENT_FAILED",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={
                    "webhook_event_id": event.event_id,
                    "authoritative_status": reconciliation.authoritative_status,
                },
            )
            logger.info(
                "Recovery case created: case_id=%s payment_id=%s",
                case.id,
                event.payment_id,
            )
        else:
            logger.info(
                "Recovery case already exists: case_id=%s payment_id=%s",
                case.id,
                event.payment_id,
            )
