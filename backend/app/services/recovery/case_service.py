import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.webhook import NormalizedPaymentFailedEvent, NormalizedPaymentLinkEvent
from app.services.policy.models import MerchantPolicy
from app.services.razorpay.reconciliation import PaymentReconciliationService
from app.services.recovery.orchestrator import OrchestrationResult, RecoveryOrchestrator

logger = logging.getLogger(__name__)


class RecoveryCaseService:
    """Create and manage recovery cases and handle lifecycle events."""

    def __init__(
        self,
        session: AsyncSession,
        reconciliation_service: PaymentReconciliationService,
        *,
        orchestrator: RecoveryOrchestrator | None = None,
        merchant_policy: MerchantPolicy | None = None,
    ) -> None:
        self._session = session
        self._repo = RecoveryCaseRepository(session)
        self._action_repo = RecoveryActionRepository(session)
        self._reconciliation = reconciliation_service
        self._orchestrator = orchestrator
        self._merchant_policy = merchant_policy

    async def handle_payment_failed(
        self,
        event: NormalizedPaymentFailedEvent,
    ) -> None:
        """Reconcile payment state and create recovery case when genuinely failed."""
        from app.services.razorpay.client import RazorpayAPIError

        try:
            reconciliation = await self._reconciliation.reconcile_payment(event.payment_id)
        except RazorpayAPIError as exc:
            logger.warning(
                "Payment reconciliation lookup failed for %s (code=%s): %s. Falling back to uncaptured/failed.",
                event.payment_id,
                exc.status_code,
                exc.message,
            )
            reconciliation = self._reconciliation.classify_without_api("failed")
        except Exception as exc:
            logger.warning(
                "Unexpected error reconciling payment %s: %s. Falling back to uncaptured/failed.",
                event.payment_id,
                exc,
            )
            reconciliation = self._reconciliation.classify_without_api("failed")

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

        # Autonomous Orchestration Bridge: If orchestrator configured, trigger pipeline for DETECTED case
        if self._orchestrator is not None and case.status == "DETECTED":
            logger.info(
                "Triggering autonomous recovery orchestration for case: case_id=%s",
                case.id,
            )
            await self._orchestrator.orchestrate_case(
                case_or_id=case.id,
                policy=self._merchant_policy,
            )


    async def orchestrate_case(
        self,
        case_id: UUID,
        policy: MerchantPolicy | None = None,
    ) -> OrchestrationResult | None:
        """Manually or programmatically trigger autonomous recovery orchestration for a case."""
        if self._orchestrator is None:
            logger.warning("Orchestrator not configured on RecoveryCaseService")
            return None
        return await self._orchestrator.orchestrate_case(
            case_id,
            policy=policy or self._merchant_policy,
        )


    async def handle_payment_link_paid(
        self,
        event: NormalizedPaymentLinkEvent,
    ) -> None:
        """Process payment_link.paid webhook deterministically and transition case to RECOVERED."""
        # 1. Correlate with RecoveryAction
        action = None
        if event.reference_id:
            action = await self._action_repo.get_by_reference_id(event.reference_id)
        if action is None and event.payment_link_id:
            action = await self._action_repo.get_by_payment_link_id(event.payment_link_id)

        if action is None:
            logger.warning(
                "RecoveryAction not found for payment_link.paid: ref=%s plink=%s",
                event.reference_id,
                event.payment_link_id,
            )
            return

        # 2. Lock RecoveryCase
        case = await self._repo.get_by_id_for_update(action.case_id)
        if case is None:
            logger.error("RecoveryCase not found for action %s", action.id)
            return

        # 3. Protected Terminal State check: if already RECOVERED, ignore
        if case.is_recovered or case.status == "RECOVERED":
            logger.info(
                "Case %s is already in protected RECOVERED state, ignoring duplicate payment_link.paid",
                case.id,
            )
            return

        # 4. Strict Financial Validations
        if event.payment_link_status != "paid":
            logger.warning("payment_link status is '%s', expected 'paid'", event.payment_link_status)
            return

        if event.amount_paid != action.amount and event.amount != action.amount:
            logger.error(
                "Amount mismatch in payment_link.paid: received %s, expected %s",
                event.amount_paid or event.amount,
                action.amount,
            )
            await self._repo.append_audit(
                case_id=case.id,
                from_state=case.status,
                to_state=case.status,
                trigger="PAYMENT_LINK_AMOUNT_MISMATCH",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={
                    "event_amount": event.amount,
                    "event_amount_paid": event.amount_paid,
                    "action_amount": action.amount,
                },
            )
            return

        if event.currency != action.currency:
            logger.error(
                "Currency mismatch in payment_link.paid: received %s, expected %s",
                event.currency,
                action.currency,
            )
            return

        # 5. Verify underlying payment entity is 'captured' (authorized != recovered)
        if event.payment_status != "captured":
            logger.warning(
                "Underlying payment status is '%s', not 'captured'. Revenue not confirmed recovered.",
                event.payment_status,
            )
            await self._repo.append_audit(
                case_id=case.id,
                from_state=case.status,
                to_state=case.status,
                trigger="PAYMENT_NOT_CAPTURED",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={
                    "payment_id": event.payment_id,
                    "payment_status": event.payment_status,
                },
            )
            return

        # 6. Atomic state transition: Action -> PAID, Case -> RECOVERED
        action = await self._action_repo.update_status(action, "PAID")

        case.is_recovered = True
        case.recovered_amount = action.amount
        case = await self._repo.update_status(
            case,
            new_status="RECOVERED",
            trigger="WEBHOOK_PAYMENT_LINK_PAID",
            actor="SYSTEM_WEBHOOK_GATEWAY",
            context_metadata={
                "action_id": str(action.id),
                "payment_link_id": event.payment_link_id,
                "reference_id": event.reference_id,
                "payment_id": event.payment_id,
                "amount_paid": action.amount,
            },
        )

        await self._repo.append_audit(
            case_id=case.id,
            from_state="RECOVERED",
            to_state="RECOVERED",
            trigger="RECOVERY_COMPLETED",
            actor="SYSTEM_WEBHOOK_GATEWAY",
            context_metadata={
                "action_id": str(action.id),
                "recovered_amount": action.amount,
            },
        )
        logger.info("Case %s successfully RECOVERED via payment link %s", case.id, event.payment_link_id)

    async def handle_payment_link_expired(
        self,
        event: NormalizedPaymentLinkEvent,
    ) -> None:
        """Process payment_link.expired webhook."""
        action = None
        if event.reference_id:
            action = await self._action_repo.get_by_reference_id(event.reference_id)
        if action is None and event.payment_link_id:
            action = await self._action_repo.get_by_payment_link_id(event.payment_link_id)

        if action is None:
            logger.warning("RecoveryAction not found for payment_link.expired: plink=%s", event.payment_link_id)
            return

        case = await self._repo.get_by_id_for_update(action.case_id)
        if case is None:
            return

        # Protected Terminal State: RECOVERED must not be overwritten
        if case.is_recovered or case.status == "RECOVERED":
            logger.info("Case %s is already in protected RECOVERED state, ignoring late payment_link.expired", case.id)
            return

        if action.status != "PAID":
            action = await self._action_repo.update_status(action, "EXPIRED")

        if case.status == "AWAITING_PAYMENT":
            case = await self._repo.update_status(
                case,
                new_status="EXPIRED",
                trigger="WEBHOOK_PAYMENT_LINK_EXPIRED",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={
                    "action_id": str(action.id),
                    "payment_link_id": event.payment_link_id,
                },
            )
        else:
            await self._repo.append_audit(
                case_id=case.id,
                from_state=case.status,
                to_state=case.status,
                trigger="PAYMENT_LINK_EXPIRED",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={"action_id": str(action.id), "payment_link_id": event.payment_link_id},
            )

    async def handle_payment_link_cancelled(
        self,
        event: NormalizedPaymentLinkEvent,
    ) -> None:
        """Process payment_link.cancelled webhook."""
        action = None
        if event.reference_id:
            action = await self._action_repo.get_by_reference_id(event.reference_id)
        if action is None and event.payment_link_id:
            action = await self._action_repo.get_by_payment_link_id(event.payment_link_id)

        if action is None:
            logger.warning("RecoveryAction not found for payment_link.cancelled: plink=%s", event.payment_link_id)
            return

        case = await self._repo.get_by_id_for_update(action.case_id)
        if case is None:
            return

        # Protected Terminal State: RECOVERED must not be overwritten
        if case.is_recovered or case.status == "RECOVERED":
            logger.info("Case %s is in protected RECOVERED state, ignoring payment_link.cancelled", case.id)
            return

        if action.status != "PAID":
            action = await self._action_repo.update_status(action, "CANCELLED")

        if case.status == "AWAITING_PAYMENT":
            case = await self._repo.update_status(
                case,
                new_status="CANCELLED",
                trigger="WEBHOOK_PAYMENT_LINK_CANCELLED",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={
                    "action_id": str(action.id),
                    "payment_link_id": event.payment_link_id,
                },
            )
        else:
            await self._repo.append_audit(
                case_id=case.id,
                from_state=case.status,
                to_state=case.status,
                trigger="PAYMENT_LINK_CANCELLED",
                actor="SYSTEM_WEBHOOK_GATEWAY",
                context_metadata={"action_id": str(action.id), "payment_link_id": event.payment_link_id},
            )

    async def handle_payment_link_partially_paid(
        self,
        event: NormalizedPaymentLinkEvent,
    ) -> None:
        """Process payment_link.partially_paid webhook without marking case recovered."""
        action = None
        if event.reference_id:
            action = await self._action_repo.get_by_reference_id(event.reference_id)
        if action is None and event.payment_link_id:
            action = await self._action_repo.get_by_payment_link_id(event.payment_link_id)

        case_id = action.case_id if action else None
        await self._repo.append_audit(
            case_id=case_id,
            from_state=None,
            to_state="AUDIT_NOTE",
            trigger="PAYMENT_LINK_PARTIALLY_PAID",
            actor="SYSTEM_WEBHOOK_GATEWAY",
            context_metadata={
                "payment_link_id": event.payment_link_id,
                "amount_paid": event.amount_paid,
                "action_amount": action.amount if action else None,
                "note": "Partial payments not accepted for complete recovery",
            },
        )
        logger.info("Recorded payment_link.partially_paid audit for link %s", event.payment_link_id)

