"""Unit tests for Multi-Action and POL-008 Policy rules."""

import pytest
from app.models.recovery_case import RecoveryCase
from app.schemas.ai import RecommendedAction, RecoveryPlan, RootCauseCategory, UrgencyLevel
from app.services.policy.engine import PolicyEngine
from app.services.policy.models import DecisionType, MerchantPolicy


def test_pol_008_high_value_escalation():
    """Verify transactions exceeding high_value_escalation_paise trigger ESCALATE decision."""
    engine = PolicyEngine()
    policy = MerchantPolicy(high_value_escalation_paise=1000000)  # ₹10,000 threshold

    case = RecoveryCase(
        payment_id="pay_high_val_001",
        amount=1500000,  # ₹15,000
        currency="INR",
        status="PLAN_GENERATED",
    )

    plan = RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.95,
        diagnostic_summary="Customer encountered OTP friction",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete checkout.",
    )

    decision = engine.evaluate(case, plan, policy)
    assert decision.decision == DecisionType.ESCALATE
    assert "REQUIRES_HUMAN_REVIEW" in decision.reason_codes


def test_pol_008_low_confidence_escalation():
    """Verify low model confidence triggers human review escalation."""
    engine = PolicyEngine()
    policy = MerchantPolicy(min_confidence_score=0.60)

    case = RecoveryCase(
        payment_id="pay_low_conf_001",
        amount=50000,  # ₹500
        currency="INR",
        status="PLAN_GENERATED",
    )

    plan = RecoveryPlan(
        root_cause_category=RootCauseCategory.TECHNICAL_GATEWAY_ERROR,
        confidence_score=0.45,  # below 0.60
        diagnostic_summary="Uncertain failure pattern",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.MEDIUM,
        link_expiry_minutes=60,
        customer_facing_message="Retry payment.",
    )

    decision = engine.evaluate(case, plan, policy)
    assert decision.decision == DecisionType.ESCALATE


def test_multi_action_whitelist_acceptance():
    """Verify all 5 multi-action recovery types pass whitelist evaluation."""
    engine = PolicyEngine()
    policy = MerchantPolicy()
    case = RecoveryCase(payment_id="pay_ma_001", amount=200000, currency="INR", status="PLAN_GENERATED")

    actions = [
        RecommendedAction.DISPATCH_PAYMENT_LINK,
        RecommendedAction.RETRY_LATER,
        RecommendedAction.CUSTOMER_ACTION,
        RecommendedAction.DO_NOT_RECOVER,
    ]

    for action in actions:
        plan = RecoveryPlan(
            root_cause_category=RootCauseCategory.USER_FRICTION,
            confidence_score=0.90,
            diagnostic_summary="Test plan",
            recommended_action=action,
            urgency=UrgencyLevel.MEDIUM,
            link_expiry_minutes=60,
            customer_facing_message="Test message",
        )
        decision = engine.evaluate(case, plan, policy)
        assert decision.is_allowed is True
