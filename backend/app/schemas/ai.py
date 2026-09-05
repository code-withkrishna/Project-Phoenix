from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


class RootCauseCategory(str, Enum):
    """Standardized root cause categories for payment failures."""

    USER_FRICTION = "USER_FRICTION"
    TECHNICAL_GATEWAY_ERROR = "TECHNICAL_GATEWAY_ERROR"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INSTRUMENT_INVALID = "INSTRUMENT_INVALID"
    TERMINAL_OR_SUSPICIOUS = "TERMINAL_OR_SUSPICIOUS"


class RecommendedAction(str, Enum):
    """Allowed recovery actions proposed by the AI."""

    DISPATCH_PAYMENT_LINK = "DISPATCH_PAYMENT_LINK"
    RETRY_LATER = "RETRY_LATER"
    CUSTOMER_ACTION = "CUSTOMER_ACTION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DO_NOT_RECOVER = "DO_NOT_RECOVER"


class UrgencyLevel(str, Enum):
    """Urgency level of recovery action."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CustomerHistoryContext(BaseModel):
    """Deterministic, non-PII customer history features."""

    prior_failures_today: int = Field(default=0, ge=0)
    lifetime_recoveries: int = Field(default=0, ge=0)
    is_repeat_customer: bool = Field(default=False)


class MerchantConstraintsContext(BaseModel):
    """Deterministic merchant constraints passed to the AI."""

    allowed_actions: list[str] = Field(
        default_factory=lambda: [
            RecommendedAction.DISPATCH_PAYMENT_LINK.value,
            RecommendedAction.RETRY_LATER.value,
            RecommendedAction.CUSTOMER_ACTION.value,
            RecommendedAction.HUMAN_REVIEW.value,
            RecommendedAction.DO_NOT_RECOVER.value,
        ]
    )
    min_link_expiry_minutes: int = Field(default=15, ge=15)
    max_link_expiry_minutes: int = Field(default=1440, le=1440)


class DiagnosticContext(BaseModel):
    """Strict diagnostic context prepared for the AI reasoner.

    CRITICAL: Contains zero raw PII (no customer phone numbers, emails,
    API credentials, or raw account details). Uses safe derived features.
    """

    amount_paise: int = Field(ge=0)
    currency: str = Field(default="INR")
    payment_id: str
    order_id: str | None = None
    payment_method: str | None = None
    error_code: str | None = None
    error_description: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    payment_status: str = Field(default="failed")
    payment_details: dict[str, Any] = Field(default_factory=dict)
    customer_history: CustomerHistoryContext = Field(default_factory=CustomerHistoryContext)
    merchant_constraints: MerchantConstraintsContext = Field(
        default_factory=MerchantConstraintsContext
    )


class RecoveryPlan(BaseModel):
    """Structured recovery plan synthesized by the AI reasoner."""

    model_config = ConfigDict(extra="forbid")

    root_cause_category: RootCauseCategory
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )
    diagnostic_summary: str = Field(
        max_length=300,
        description="Concise diagnostic summary of failure root cause",
    )
    recommended_action: RecommendedAction
    urgency: UrgencyLevel
    link_expiry_minutes: int = Field(
        ge=15,
        le=1440,
        description="Proposed payment link expiry duration in minutes",
    )
    customer_facing_message: str = Field(
        max_length=160,
        description="Customer-facing notification message",
    )
    expected_net_recovery_paise: int | None = Field(
        default=None,
        description="Calculated Expected Net Recovery in paise",
    )
    recovery_probability: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Estimated probability of successful recovery",
    )
    economic_reasons: list[str] = Field(
        default_factory=list,
        description="Transparent economic and decision reasoning factors",
    )


class AIDiagnosisMetadata(BaseModel):
    """Diagnostic execution metadata for latency, tokens, and model tracking."""

    model_name: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int = 0
    raw_response: dict[str, Any] | str = Field(default_factory=dict)


class AIDiagnosisSummary(ORMModel):
    """Summary of persisted AI diagnosis for API detail view."""

    id: UUID
    case_id: UUID
    model_name: str
    root_cause_category: str
    confidence_score: float
    diagnostic_summary: str
    recommended_action: str
    suggested_expiry_minutes: int | None = None
    suggested_customer_note: str | None = None
    raw_response: dict[str, Any] | None = None
    created_at: datetime

