"""Recovery Orchestration Bridge for Project Phoenix.

Connects:
Recovery Case Detection -> Context Engine -> AI Recovery Planner ->
Policy Engine -> Execution Guard -> Recovery Executor -> Razorpay Payment Link
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.services.ai.base import AIProvider
from app.services.policy.engine import PolicyEngine
from app.services.policy.models import MerchantPolicy
from app.services.razorpay.client import RazorpayClient
from app.services.recovery.execution_guard import ExecutionGuard
from app.services.recovery.executor import ExecutionResult, RecoveryExecutor
from app.services.recovery.planner import AIRecoveryPlanner, PlannerResult

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"RECOVERED", "RESOLVED_EXTERNALLY", "CANCELLED", "EXPIRED"})
ACTIVE_EXECUTION_STATES = frozenset({"AWAITING_PAYMENT", "EXECUTING"})
PAUSED_STATES = frozenset({"POLICY_REJECTED", "ESCALATED"})


@dataclass
class OrchestrationResult:
    """Outcome of full autonomous recovery orchestration."""

    success: bool
    stage: str  # "PLANNING", "EXECUTION", "TERMINAL", "ALREADY_ACTIVE", "PAUSED"
    case: RecoveryCase
    planner_result: PlannerResult | None = None
    execution_result: ExecutionResult | None = None
    error: str | None = None


class RecoveryOrchestrator:
    """Orchestrates end-to-end autonomous recovery lifecycle.

    Guarantees:
        1. AI advises; deterministic policy gates; deterministic executor acts.
        2. Zero direct Razorpay API access from LLM.
        3. Strict two-tier idempotency & fail-closed error handling.
        4. Complete, immutable audit trail.
    """

    def __init__(
        self,
        session: AsyncSession,
        razorpay_client: RazorpayClient | None = None,
        *,
        ai_provider: AIProvider | None = None,
        policy_engine: PolicyEngine | None = None,
        execution_guard: ExecutionGuard | None = None,
        planner: AIRecoveryPlanner | None = None,
        executor: RecoveryExecutor | None = None,
    ) -> None:
        self._session = session
        self._case_repo = RecoveryCaseRepository(session)
        self._planner = planner or AIRecoveryPlanner(session, ai_provider=ai_provider)
        if executor is not None:
            self._executor = executor
        elif razorpay_client is not None:
            self._executor = RecoveryExecutor(
                session,
                razorpay_client=razorpay_client,
                policy_engine=policy_engine,
                execution_guard=execution_guard,
            )
        else:
            self._executor = None

    async def orchestrate_case(
        self,
        case_or_id: RecoveryCase | UUID,
        policy: MerchantPolicy | None = None,
        *,
        current_time: datetime | None = None,
    ) -> OrchestrationResult:
        """Execute autonomous recovery pipeline for a given recovery case."""
        case_id = case_or_id.id if isinstance(case_or_id, RecoveryCase) else case_or_id

        # 1. Lock and inspect RecoveryCase
        case = await self._case_repo.get_by_id_for_update(case_id)
        if case is None:
            raise ValueError(f"RecoveryCase with id {case_id} not found")

        # 2. Guard against terminal or already-active states (Idempotency invariant)
        if case.is_recovered or case.status in TERMINAL_STATES:
            logger.info(
                "Orchestration skipped: Case %s is in terminal state %s",
                case.id,
                case.status,
            )
            return OrchestrationResult(
                success=True,
                stage="TERMINAL",
                case=case,
                error=f"Case is in terminal state {case.status}",
            )

        if case.status in ACTIVE_EXECUTION_STATES:
            logger.info(
                "Orchestration skipped: Case %s already active in %s",
                case.id,
                case.status,
            )
            return OrchestrationResult(
                success=True,
                stage="ALREADY_ACTIVE",
                case=case,
                error=f"Case already active in {case.status}",
            )

        if case.status in PAUSED_STATES:
            logger.info(
                "Orchestration skipped: Case %s is in paused state %s",
                case.id,
                case.status,
            )
            return OrchestrationResult(
                success=False,
                stage="PAUSED",
                case=case,
                error=f"Case is paused in {case.status}",
            )

        # 3. Log Orchestration Start in Audit Ledger
        await self._case_repo.append_audit(
            case_id=case.id,
            from_state=case.status,
            to_state=case.status,
            trigger="ORCHESTRATION_STARTED",
            actor="RECOVERY_ORCHESTRATOR",
            context_metadata={"initial_status": case.status},
        )

        # 4. Stage 1: AI Diagnosis & Planning (ContextEngine -> LLM Provider -> RecoveryPlan)
        planner_result = await self._planner.plan_recovery(case)
        if not planner_result.success or planner_result.plan is None:
            logger.warning(
                "Orchestration halted at AI planning stage for case %s: %s",
                case.id,
                planner_result.error,
            )
            return OrchestrationResult(
                success=False,
                stage="PLANNING",
                case=planner_result.case,
                planner_result=planner_result,
                error=planner_result.error,
            )

        # 5. Stage 2: Policy Gate -> Execution Guard -> Deterministic Razorpay Execution
        if self._executor is None:
            raise RuntimeError("RecoveryExecutor is not configured for RecoveryOrchestrator")

        exec_result = await self._executor.execute_recovery(
            planner_result.case,
            planner_result.plan,
            policy=policy,
            current_time=current_time,
        )

        if not exec_result.success:
            logger.warning(
                "Orchestration halted at execution stage for case %s: %s",
                case.id,
                exec_result.error,
            )
            return OrchestrationResult(
                success=False,
                stage="EXECUTION",
                case=exec_result.case,
                planner_result=planner_result,
                execution_result=exec_result,
                error=exec_result.error,
            )

        logger.info(
            "Orchestration completed successfully for case %s: status=%s",
            case.id,
            exec_result.case.status,
        )
        return OrchestrationResult(
            success=True,
            stage="EXECUTION",
            case=exec_result.case,
            planner_result=planner_result,
            execution_result=exec_result,
        )
