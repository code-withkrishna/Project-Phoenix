"""Recovery Execution Orchestrator for Wave 3."""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import RecoveryPlan
from app.services.policy.engine import PolicyEngine
from app.services.policy.models import DecisionType, MerchantPolicy, PolicyDecision
from app.services.razorpay.client import RazorpayAPIError, RazorpayClient
from app.services.recovery.execution_guard import ExecutionGuard
from app.services.recovery.reference import generate_reference_id

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of recovery execution."""

    success: bool
    case: RecoveryCase
    action: RecoveryAction | None = None
    decision: PolicyDecision | None = None
    error: str | None = None


class RecoveryExecutor:
    """Orchestrates policy gating, execution guard verification, and idempotent Razorpay Payment Link creation."""

    def __init__(
        self,
        session: AsyncSession,
        razorpay_client: RazorpayClient,
        *,
        policy_engine: PolicyEngine | None = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> None:
        self._session = session
        self._razorpay_client = razorpay_client
        self._case_repo = RecoveryCaseRepository(session)
        self._action_repo = RecoveryActionRepository(session)
        self._policy_engine = policy_engine or PolicyEngine()
        self._execution_guard = execution_guard or ExecutionGuard()

    async def execute_recovery(
        self,
        case_or_id: RecoveryCase | UUID,
        plan: RecoveryPlan,
        policy: MerchantPolicy | None = None,
        *,
        current_time: datetime | None = None,
    ) -> ExecutionResult:
        """Execute recovery pipeline from RecoveryPlan to Payment Link creation."""
        now = current_time or datetime.now(timezone.utc)
        effective_policy = policy or MerchantPolicy()

        case_id = case_or_id.id if isinstance(case_or_id, RecoveryCase) else case_or_id

        # 1. Concurrency control: Lock RecoveryCase row
        case = await self._case_repo.get_by_id_for_update(case_id)
        if case is None:
            raise ValueError(f"RecoveryCase with id {case_id} not found")

        # Fetch prior actions for this case
        prior_actions = await self._action_repo.list_by_case_id(case.id)
        attempt_number = len(prior_actions) + 1

        # 2. Evaluate Policy Engine
        decision = self._policy_engine.evaluate(
            case=case,
            plan=plan,
            policy=effective_policy,
            prior_actions=prior_actions,
            current_time=now,
        )

        await self._case_repo.append_audit(
            case_id=case.id,
            from_state=case.status,
            to_state=case.status,
            trigger="POLICY_EVALUATED",
            actor="DETERMINISTIC_POLICY_ENGINE",
            context_metadata={
                "decision": decision.decision.value,
                "reason_codes": decision.reason_codes,
                "evaluated_rules_count": len(decision.evaluated_rules),
            },
        )

        if decision.decision == DecisionType.ESCALATE:
            target_status = "ESCALATED"
            case = await self._case_repo.update_status(
                case,
                new_status=target_status,
                trigger="POLICY_ESCALATED_HITL",
                actor="DETERMINISTIC_POLICY_ENGINE",
                context_metadata={
                    "reason_codes": decision.reason_codes,
                    "violations": [v.message for v in decision.violations],
                    "escalation_note": "Case escalated for merchant human review.",
                },
            )
            return ExecutionResult(
                success=False,
                case=case,
                decision=decision,
                error="Case escalated for manual merchant human review",
            )

        if decision.decision != DecisionType.ALLOW:
            target_status = "POLICY_REJECTED"
            case = await self._case_repo.update_status(
                case,
                new_status=target_status,
                trigger="POLICY_REJECTED",
                actor="DETERMINISTIC_POLICY_ENGINE",
                context_metadata={
                    "reason_codes": decision.reason_codes,
                    "violations": [v.message for v in decision.violations],
                },
            )
            return ExecutionResult(
                success=False,
                case=case,
                decision=decision,
                error=f"Policy rejected: {decision.reason_codes}",
            )

        # Policy passed: transition to POLICY_APPROVED
        case = await self._case_repo.update_status(
            case,
            new_status="POLICY_APPROVED",
            trigger="POLICY_APPROVED",
            actor="DETERMINISTIC_POLICY_ENGINE",
            context_metadata={"action": decision.action},
        )

        # Handle non-financial or deferred actions cleanly
        if decision.action in ("DO_NOT_RECOVER", "RETRY_LATER", "CUSTOMER_ACTION", "HUMAN_REVIEW"):
            return ExecutionResult(
                success=True,
                case=case,
                decision=decision,
            )

        # 3. Generate deterministic compact reference ID
        reference_id = generate_reference_id(case.id, sequence=attempt_number)

        # 4. Execution Guard verification
        guard_result = self._execution_guard.verify(
            case=case,
            plan=plan,
            decision=decision,
            policy=effective_policy,
            prior_actions=prior_actions,
            reference_id=reference_id,
            attempt_number=attempt_number,
        )

        if not guard_result.allowed:
            logger.warning(
                "ExecutionGuard blocked execution for case %s: check #%s %s",
                case.id,
                guard_result.check_failed,
                guard_result.reason,
            )
            case = await self._case_repo.update_status(
                case,
                new_status="ESCALATED",
                trigger="EXECUTION_GUARD_FAILED",
                actor="EXECUTION_GUARD",
                context_metadata={
                    "check_failed": guard_result.check_failed,
                    "reason": guard_result.reason,
                },
            )
            return ExecutionResult(
                success=False,
                case=case,
                decision=decision,
                error=f"ExecutionGuard blocked: {guard_result.reason}",
            )

        # 5. Transition to EXECUTING
        case = await self._case_repo.update_status(
            case,
            new_status="EXECUTING",
            trigger="EXECUTION_STARTED",
            actor="RECOVERY_EXECUTOR",
            context_metadata={"reference_id": reference_id, "attempt_number": attempt_number},
        )

        # 6. Create initial PENDING RecoveryAction record
        expiry_minutes = plan.link_expiry_minutes or 30
        expires_at = now + timedelta(minutes=expiry_minutes)
        action = await self._action_repo.create(
            case_id=case.id,
            reference_id=reference_id,
            amount=case.amount,
            currency=case.currency,
            expires_at=expires_at,
            action_type="CREATE_PAYMENT_LINK",
            attempt_number=attempt_number,
            status="PENDING",
        )

        await self._case_repo.append_audit(
            case_id=case.id,
            from_state="EXECUTING",
            to_state="EXECUTING",
            trigger="RECOVERY_ACTION_CREATED",
            actor="RECOVERY_EXECUTOR",
            context_metadata={
                "action_id": str(action.id),
                "reference_id": reference_id,
                "amount": action.amount,
            },
        )

        # 7. Execute Razorpay Call with Timeout Reconciliation
        expire_by_unix = int(now.timestamp()) + (expiry_minutes * 60)
        customer_payload = {}
        if case.customer_email:
            customer_payload["email"] = case.customer_email
        if case.customer_phone:
            customer_payload["contact"] = case.customer_phone

        description = plan.customer_facing_message or "Complete your order with instant 1-click payment."

        plink_data: dict | None = None
        is_reconciled = False

        try:
            plink_data = await self._razorpay_client.create_payment_link(
                amount=case.amount,
                currency=case.currency,
                reference_id=reference_id,
                description=description,
                expire_by=expire_by_unix,
                customer=customer_payload if customer_payload else None,
                notes={
                    "phoenix_case_id": str(case.id),
                    "original_payment_id": case.payment_id,
                },
            )
        except RazorpayAPIError as exc:
            # Check for ambiguous outcome: Timeout (408) or Network Error
            if exc.status_code == 408:
                logger.warning(
                    "POST /v1/payment_links timed out for case %s, ref %s. Initiating timeout reconciliation.",
                    case.id,
                    reference_id,
                )
                try:
                    reconciled_link = await self._razorpay_client.get_payment_link_by_reference(reference_id)
                    if reconciled_link is not None:
                        logger.info("Found existing payment link during timeout reconciliation: %s", reconciled_link.get("id"))
                        plink_data = reconciled_link
                        is_reconciled = True
                    else:
                        logger.error("Timeout reconciliation found no payment link for reference %s", reference_id)
                except Exception as recon_exc:
                    logger.error("Timeout reconciliation query failed: %s", recon_exc)

            if plink_data is None:
                # Controlled failure
                action = await self._action_repo.update_status(action, "FAILED")
                case = await self._case_repo.update_status(
                    case,
                    new_status="FAILED",
                    trigger="EXECUTION_FAILED",
                    actor="RAZORPAY_EXECUTOR",
                    context_metadata={
                        "error": str(exc),
                        "status_code": exc.status_code,
                        "reference_id": reference_id,
                    },
                )
                return ExecutionResult(
                    success=False,
                    case=case,
                    action=action,
                    decision=decision,
                    error=f"Razorpay execution failed: {exc}",
                )
        except Exception as exc:
            logger.error("Unexpected error during payment link dispatch: %s", exc)
            action = await self._action_repo.update_status(action, "FAILED")
            case = await self._case_repo.update_status(
                case,
                new_status="FAILED",
                trigger="EXECUTION_FAILED",
                actor="RAZORPAY_EXECUTOR",
                context_metadata={"error": str(exc), "reference_id": reference_id},
            )
            return ExecutionResult(
                success=False,
                case=case,
                action=action,
                decision=decision,
                error=f"Execution error: {exc}",
            )

        # 8. Success: Update RecoveryAction to ISSUED and RecoveryCase to AWAITING_PAYMENT
        payment_link_id = plink_data.get("id")
        payment_link_url = plink_data.get("short_url")
        executed_at = datetime.now(timezone.utc)

        action = await self._action_repo.update_status(
            action,
            "ISSUED",
            payment_link_id=payment_link_id,
            payment_link_url=payment_link_url,
            executed_at=executed_at,
        )

        audit_trigger = "RAZORPAY_PAYMENT_LINK_RECONCILED" if is_reconciled else "RAZORPAY_PAYMENT_LINK_CREATED"
        await self._case_repo.append_audit(
            case_id=case.id,
            from_state="EXECUTING",
            to_state="EXECUTING",
            trigger=audit_trigger,
            actor="RAZORPAY_EXECUTOR",
            context_metadata={
                "payment_link_id": payment_link_id,
                "payment_link_url": payment_link_url,
                "reference_id": reference_id,
                "is_reconciled": is_reconciled,
            },
        )

        case = await self._case_repo.update_status(
            case,
            new_status="AWAITING_PAYMENT",
            trigger="PAYMENT_LINK_ISSUED",
            actor="RAZORPAY_EXECUTOR",
            context_metadata={
                "action_id": str(action.id),
                "payment_link_id": payment_link_id,
                "payment_link_url": payment_link_url,
            },
        )

        return ExecutionResult(
            success=True,
            case=case,
            action=action,
            decision=decision,
        )
