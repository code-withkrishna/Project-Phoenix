"""Unit tests for AI schemas and strict validation constraints."""

import pytest
from pydantic import ValidationError

from app.schemas.ai import (
    CustomerHistoryContext,
    DiagnosticContext,
    MerchantConstraintsContext,
    RecommendedAction,
    RecoveryPlan,
    RootCauseCategory,
    UrgencyLevel,
)


def test_valid_recovery_plan():
    """Verify a properly formatted RecoveryPlan passes validation."""
    plan = RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.95,
        diagnostic_summary="Customer cancelled during UPI checkout due to timeout.",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete your payment using this 1-click link.",
    )
    assert plan.root_cause_category == RootCauseCategory.USER_FRICTION
    assert plan.confidence_score == 0.95
    assert plan.link_expiry_minutes == 30
    assert plan.recommended_action == RecommendedAction.DISPATCH_PAYMENT_LINK


def test_recovery_plan_invalid_confidence_score():
    """Confidence score must be strictly between 0.0 and 1.0."""
    with pytest.raises(ValidationError) as exc:
        RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=1.5,  # Invalid: > 1.0
            diagnostic_summary="Summary",
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=30,
            customer_facing_message="Message",
        )
    assert "confidence_score" in str(exc.value)

    with pytest.raises(ValidationError):
        RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=-0.1,  # Invalid: < 0.0
            diagnostic_summary="Summary",
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=30,
            customer_facing_message="Message",
        )


def test_recovery_plan_expiry_bounds():
    """Expiry minutes must be >= 15 (Razorpay minimum) and <= 1440 (24h)."""
    with pytest.raises(ValidationError) as exc:
        RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=0.9,
            diagnostic_summary="Summary",
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=10,  # Invalid: < 15
            customer_facing_message="Message",
        )
    assert "link_expiry_minutes" in str(exc.value)

    with pytest.raises(ValidationError):
        RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=0.9,
            diagnostic_summary="Summary",
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=1500,  # Invalid: > 1440
            customer_facing_message="Message",
        )


def test_recovery_plan_message_length_limit():
    """Customer-facing message must not exceed 160 characters."""
    with pytest.raises(ValidationError) as exc:
        RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=0.9,
            diagnostic_summary="Summary",
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=30,
            customer_facing_message="A" * 161,  # Invalid: 161 chars
        )
    assert "customer_facing_message" in str(exc.value)


def test_recovery_plan_summary_length_limit():
    """Diagnostic summary must not exceed 300 characters."""
    with pytest.raises(ValidationError) as exc:
        RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=0.9,
            diagnostic_summary="B" * 301,  # Invalid: 301 chars
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=30,
            customer_facing_message="Message",
        )
    assert "diagnostic_summary" in str(exc.value)


def test_recovery_plan_extra_fields_forbidden():
    """Extra unrecognized fields must be rejected (extra='forbid')."""
    with pytest.raises(ValidationError) as exc:
        RecoveryPlan.model_validate(
            {
                "root_cause_category": "USER_FRICTION",
                "confidence_score": 0.9,
                "diagnostic_summary": "Summary",
                "recommended_action": "DISPATCH_PAYMENT_LINK",
                "urgency": "HIGH",
                "link_expiry_minutes": 30,
                "customer_facing_message": "Message",
                "unauthorized_field": "injected_data",  # Extra field
            }
        )
    assert "extra_forbidden" in str(exc.value) or "unauthorized_field" in str(exc.value)


def test_recovery_plan_invalid_enum_rejected():
    """Invalid enum values for root cause, action, and urgency must be rejected."""
    with pytest.raises(ValidationError):
        RecoveryPlan(
            root_cause_category="UNKNOWN_CATEGORY",  # type: ignore
            confidence_score=0.9,
            diagnostic_summary="Summary",
            recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
            urgency=UrgencyLevel.HIGH,
            link_expiry_minutes=30,
            customer_facing_message="Message",
        )


def test_diagnostic_context_schema():
    """DiagnosticContext encapsulates telemetry, derived customer metrics, and constraints."""
    context = DiagnosticContext(
        amount_paise=499900,
        currency="INR",
        payment_id="pay_test_123",
        order_id="order_test_456",
        payment_method="upi",
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment cancelled by user",
        error_source="customer",
        error_step="payment_authentication",
        error_reason="payment_cancelled",
        payment_status="failed",
        payment_details={"vpa_handle": "***@okhdfcbank"},
        customer_history=CustomerHistoryContext(
            prior_failures_today=1,
            lifetime_recoveries=2,
            is_repeat_customer=True,
        ),
        merchant_constraints=MerchantConstraintsContext(
            allowed_actions=["DISPATCH_PAYMENT_LINK", "DO_NOT_RECOVER"],
            min_link_expiry_minutes=15,
            max_link_expiry_minutes=1440,
        ),
    )
    assert context.amount_paise == 499900
    assert context.customer_history.is_repeat_customer is True
    assert context.merchant_constraints.min_link_expiry_minutes == 15
