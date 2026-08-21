"""Deterministic Execution Guard for financial recovery execution."""

from dataclasses import dataclass
import logging
from typing import Sequence

from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.schemas.ai import RecoveryPlan
from app.services.policy.models import DecisionType, MerchantPolicy, PolicyDecision

logger = logging.getLogger(__name__)

VALID_CASE_STATES_FOR_EXECUTION = frozenset({"PLAN_GENERATED", "POLICY_APPROVED"})
PERMITTED_ACTIONS = frozenset({"CREATE_PAYMENT_LINK", "DISPATCH_PAYMENT_LINK"})


class ExecutionGuardError(Exception):
    """Raised when an execution invariant is violated."""

    def __init__(self, check_number: int, check_name: str, message: str) -> None:
        self.check_number = check_number
        self.check_name = check_name
        self.message = message
        super().__init__(f"ExecutionGuard Check #{check_number} ({check_name}) FAILED: {message}")


@dataclass
class ExecutionGuardResult:
    """Outcome of Execution Guard verification."""

    allowed: bool
    reason: str | None = None
    check_failed: int | None = None


class ExecutionGuard:
    """Deterministic final gate immediately before external Razorpay financial calls."""

    def verify(
        self,
        case: RecoveryCase | None,
        plan: RecoveryPlan | None,
        decision: PolicyDecision | None,
        policy: MerchantPolicy | None,
        prior_actions: Sequence[RecoveryAction] | None,
        reference_id: str,
        attempt_number: int,
    ) -> ExecutionGuardResult:
        """Verify all 14 mandatory deterministic invariants before calling Razorpay."""
        # 1. Case exists
        if case is None:
            return ExecutionGuardResult(False, "Case does not exist", 1)

        # 2. Case is in the correct state
        if case.status not in VALID_CASE_STATES_FOR_EXECUTION:
            return ExecutionGuardResult(
                False,
                f"Case status '{case.status}' not in permitted execution states {list(VALID_CASE_STATES_FOR_EXECUTION)}",
                2,
            )

        # 3. Case is not already recovered
        if case.is_recovered or case.status == "RECOVERED":
            return ExecutionGuardResult(False, "Case is already recovered", 3)

        # 4. RecoveryPlan exists
        if plan is None:
            return ExecutionGuardResult(False, "RecoveryPlan does not exist", 4)

        # 5. RecoveryPlan is valid
        if not hasattr(plan, "recommended_action") or not hasattr(plan, "link_expiry_minutes"):
            return ExecutionGuardResult(False, "RecoveryPlan is malformed or invalid", 5)

        # 6. PolicyDecision = ALLOW
        if decision is None or decision.decision != DecisionType.ALLOW:
            dec_val = decision.decision.value if decision else "None"
            return ExecutionGuardResult(
                False,
                f"PolicyDecision is '{dec_val}', expected ALLOW",
                6,
            )

        # 7. Action is permitted
        plan_action = plan.recommended_action.value if hasattr(plan.recommended_action, "value") else str(plan.recommended_action)
        if plan_action not in PERMITTED_ACTIONS and decision.action not in PERMITTED_ACTIONS:
            return ExecutionGuardResult(
                False,
                f"Action '{plan_action}' is not permitted for financial execution",
                7,
            )

        # 8. Amount comes from authoritative case/payment data
        if case.amount is None or case.amount <= 0:
            return ExecutionGuardResult(
                False,
                f"Authoritative case amount is invalid: {case.amount}",
                8,
            )

        # 9. Currency matches
        if case.currency != "INR":
            return ExecutionGuardResult(
                False,
                f"Currency '{case.currency}' is not supported (expected INR)",
                9,
            )

        # 10. Expiry is within policy
        expiry = plan.link_expiry_minutes
        min_exp = policy.min_link_expiry_minutes if policy else 15
        max_exp = policy.max_link_expiry_minutes if policy else 1440
        if expiry is None or not (min_exp <= expiry <= max_exp):
            return ExecutionGuardResult(
                False,
                f"Expiry {expiry}m is outside permitted bounds [{min_exp}m, {max_exp}m]",
                10,
            )

        # 11. No active duplicate RecoveryAction exists
        actions = list(prior_actions or [])
        active_actions = [a for a in actions if a.status in {"PENDING", "ISSUED"}]
        if active_actions:
            return ExecutionGuardResult(
                False,
                f"Active RecoveryAction already exists for case: action_id={active_actions[0].id}, status={active_actions[0].status}",
                11,
            )

        # 12. Reference ID <= 40 characters
        if not reference_id or len(reference_id) > 40:
            return ExecutionGuardResult(
                False,
                f"Reference ID '{reference_id}' exceeds 40 characters or is empty",
                12,
            )

        # 13. Case is not concurrently being executed
        if case.status == "EXECUTING":
            return ExecutionGuardResult(
                False,
                "Case is currently marked as EXECUTING",
                13,
            )

        # 14. Recovery attempt limits have not been exceeded
        max_retries = policy.max_retry_attempts if policy else 2
        completed_or_active_count = len([a for a in actions if a.status in {"PENDING", "ISSUED", "PAID"}])
        if completed_or_active_count >= max_retries or attempt_number > max_retries:
            return ExecutionGuardResult(
                False,
                f"Recovery attempt limit exceeded: attempts={completed_or_active_count}, max={max_retries}",
                14,
            )

        return ExecutionGuardResult(allowed=True)
