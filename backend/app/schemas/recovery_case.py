"""Recovery case API schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.ai import AIDiagnosisSummary
from app.schemas.common import AuditLogEntry, ORMModel, PaginatedResponse


class RecoveryCaseSummary(ORMModel):
    """Summary representation for list endpoints."""

    id: UUID
    payment_id: str
    order_id: str | None = None
    amount: int
    currency: str
    customer_email: str | None = None
    customer_phone: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    status: str
    is_recovered: bool = False
    recovered_amount: int = 0
    created_at: datetime
    updated_at: datetime


class RecoveryActionSummary(ORMModel):
    """Summary representation of a RecoveryAction."""

    id: UUID
    case_id: UUID
    action_type: str
    reference_id: str
    payment_link_id: str | None = None
    payment_link_url: str | None = None
    amount: int
    currency: str
    status: str
    attempt_number: int
    expires_at: datetime
    created_at: datetime
    executed_at: datetime | None = None


class RecoveryCaseDetail(RecoveryCaseSummary):
    """Detailed recovery case including telemetry, audit trail, and actions."""

    failure_telemetry: dict = Field(default_factory=dict)
    audit_trail: list[AuditLogEntry] = Field(default_factory=list)
    ai_diagnosis: AIDiagnosisSummary | None = None
    recovery_actions: list[RecoveryActionSummary] = Field(default_factory=list)


class RecoveryCaseListResponse(PaginatedResponse[RecoveryCaseSummary]):
    """Paginated recovery case list response."""

    pass

