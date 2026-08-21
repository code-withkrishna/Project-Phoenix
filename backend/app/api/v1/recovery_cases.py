"""Recovery case REST endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.common import AuditLogEntry
from app.schemas.recovery_case import (
    RecoveryCaseDetail,
    RecoveryCaseListResponse,
    RecoveryCaseSummary,
)

router = APIRouter(prefix="/recovery-cases", tags=["recovery-cases"])


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
    """Retrieve a single recovery case with audit trail."""
    repo = RecoveryCaseRepository(session)
    case = await repo.get_by_id(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    audit_trail = await repo.get_audit_trail(case_id)
    detail = RecoveryCaseDetail.model_validate(case)
    detail.audit_trail = [AuditLogEntry.model_validate(entry) for entry in audit_trail]
    return detail
