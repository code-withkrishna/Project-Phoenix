"""Recovery case REST endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.repositories.ai_diagnoses import AIDiagnosisRepository
from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import AIDiagnosisSummary, RecommendedAction, RecoveryPlan, RootCauseCategory, UrgencyLevel
from app.schemas.common import AuditLogEntry
from app.schemas.recovery_case import (
    RecoveryActionSummary,
    RecoveryCaseDetail,
    RecoveryCaseListResponse,
    RecoveryCaseSummary,
)
from app.services.razorpay.client import RazorpayClient
from app.services.recovery.executor import RecoveryExecutor

router = APIRouter(prefix="/recovery-cases", tags=["recovery-cases"])


class HITLActionRequest(BaseModel):
    """Payload for merchant manual override or review."""

    reason: str = Field(default="Merchant approved via Dashboard HITL interface", max_length=255)
    action: str = Field(default="CREATE_PAYMENT_LINK")
    link_expiry_minutes: int = Field(default=60, ge=15, le=1440)
    customer_note: str | None = Field(default=None, max_length=160)


@router.get("", response_model=RecoveryCaseListResponse)
async def list_recovery_cases(
    status: str | None = Query(default=None),
    payment_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> RecoveryCaseListResponse:
    """List recovery cases with optional filters."""
    repo = RecoveryCaseRepository(session)
    cases, total = await repo.list_cases(
        status=status,
        payment_id=payment_id,
        page=page,
        page_size=page_size,
    )
    return RecoveryCaseListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[RecoveryCaseSummary.model_validate(case) for case in cases],
    )


@router.get("/{case_id}", response_model=RecoveryCaseDetail)
async def get_recovery_case(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> RecoveryCaseDetail:
    """Retrieve a single recovery case with audit trail, AI diagnosis, and recovery actions."""
    repo = RecoveryCaseRepository(session)
    case = await repo.get_by_id(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    audit_trail = await repo.get_audit_trail(case_id)
    detail = RecoveryCaseDetail.model_validate(case)
    detail.audit_trail = [AuditLogEntry.model_validate(entry) for entry in audit_trail]

    diag_repo = AIDiagnosisRepository(session)
    latest_diagnosis = await diag_repo.get_latest_by_case_id(case_id)
    if latest_diagnosis is not None:
        detail.ai_diagnosis = AIDiagnosisSummary.model_validate(latest_diagnosis)

    action_repo = RecoveryActionRepository(session)
    actions = await action_repo.list_by_case_id(case_id)
    detail.recovery_actions = [RecoveryActionSummary.model_validate(action) for action in actions]

    return detail


@router.post("/{case_id}/approve", response_model=RecoveryCaseDetail)
async def approve_recovery_case(
    case_id: uuid.UUID,
    req: HITLActionRequest = HITLActionRequest(),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecoveryCaseDetail:
    """Merchant manual approval of an escalated or pending recovery case."""
    repo = RecoveryCaseRepository(session)
    case = await repo.get_by_id_for_update(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    if case.status not in ("ESCALATED", "PLAN_GENERATED", "DETECTED"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve case in state '{case.status}'",
        )

    # Log merchant manual approval
    await repo.append_audit(
        case_id=case.id,
        from_state=case.status,
        to_state="POLICY_APPROVED",
        trigger="MERCHANT_HITL_APPROVED",
        actor="MERCHANT_OPERATOR",
        context_metadata={"reason": req.reason, "approved_action": req.action},
    )

    client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(session, razorpay_client=client)
        diag_repo = AIDiagnosisRepository(session)
        latest_diag = await diag_repo.get_latest_by_case_id(case_id)

        root_cause = RootCauseCategory(latest_diag.root_cause_category) if latest_diag else RootCauseCategory.USER_FRICTION
        conf = latest_diag.confidence_score if latest_diag else 0.90
        summary = latest_diag.diagnostic_summary if latest_diag else "Approved by merchant operator"

        manual_plan = RecoveryPlan(
            root_cause_category=root_cause,
            confidence_score=conf,
            diagnostic_summary=summary,
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=req.link_expiry_minutes,
            customer_facing_message=req.customer_note or "Merchant-approved recovery payment link.",
        )

        from app.services.policy.models import MerchantPolicy
        override_policy = MerchantPolicy(
            high_value_escalation_paise=0,
            min_confidence_score=0.0,
        )

        exec_res = await executor.execute_recovery(case, manual_plan, policy=override_policy)
        if not exec_res.success:
            raise HTTPException(status_code=500, detail=f"Execution failed: {exec_res.error}")

    finally:
        await client.close()

    return await get_recovery_case(case_id, session)


@router.post("/{case_id}/reject", response_model=RecoveryCaseDetail)
async def reject_recovery_case(
    case_id: uuid.UUID,
    req: HITLActionRequest = HITLActionRequest(reason="Rejected by merchant operator"),
    session: AsyncSession = Depends(get_db),
) -> RecoveryCaseDetail:
    """Merchant manual rejection/cancellation of recovery."""
    repo = RecoveryCaseRepository(session)
    case = await repo.get_by_id_for_update(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    if case.status in ("RECOVERED", "CANCELLED", "RESOLVED_EXTERNALLY"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject case in terminal state '{case.status}'",
        )

    case = await repo.update_status(
        case,
        new_status="CANCELLED",
        trigger="MERCHANT_HITL_REJECTED",
        actor="MERCHANT_OPERATOR",
        context_metadata={"reason": req.reason},
    )

    return await get_recovery_case(case_id, session)
