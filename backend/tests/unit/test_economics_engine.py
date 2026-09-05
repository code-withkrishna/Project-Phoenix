"""Unit tests for Economic Decision Engine."""

import pytest

from app.schemas.ai import (
    CustomerHistoryContext,
    DiagnosticContext,
    RecommendedAction,
    RootCauseCategory,
)
from app.services.recovery.economics import EconomicDecisionEngine


def test_positive_expected_net_recovery():
    """Verify positive ENR calculation for standard transaction."""
    engine = EconomicDecisionEngine()
    context = DiagnosticContext(
        amount_paise=299900,  # ₹2,999
        currency="INR",
        payment_id="pay_test_001",
        customer_history=CustomerHistoryContext(prior_failures_today=0, is_repeat_customer=True, lifetime_recoveries=3),
    )

    eval_result = engine.evaluate(
        context=context,
        root_cause=RootCauseCategory.USER_FRICTION,
        raw_confidence=0.95,
        proposed_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
    )

    assert eval_result.is_economically_viable is True
    assert eval_result.expected_net_recovery_paise > 200000
    assert eval_result.recommended_action == "DISPATCH_PAYMENT_LINK"
    assert any("Positive expected net recovery" in r for r in eval_result.reasons)


def test_security_risk_negative_enr():
    """Verify security risk applies severe risk penalty and halts recovery."""
    engine = EconomicDecisionEngine()
    context = DiagnosticContext(
        amount_paise=500000,
        currency="INR",
        payment_id="pay_test_002",
        error_reason="fraud_detected",
    )

    eval_result = engine.evaluate(
        context=context,
        root_cause=RootCauseCategory.TERMINAL_OR_SUSPICIOUS,
        raw_confidence=0.99,
        proposed_action=RecommendedAction.DO_NOT_RECOVER,
    )

    assert eval_result.is_economically_viable is False
    assert eval_result.recommended_action == "DO_NOT_RECOVER"
    assert eval_result.risk_penalty_paise > 0


def test_repeat_failures_friction_penalty():
    """Verify customer friction penalty increases with multiple failures today."""
    engine = EconomicDecisionEngine()
    context_repeat = DiagnosticContext(
        amount_paise=100000,
        currency="INR",
        payment_id="pay_test_003",
        customer_history=CustomerHistoryContext(prior_failures_today=3, is_repeat_customer=False),
    )

    eval_result = engine.evaluate(
        context=context_repeat,
        root_cause=RootCauseCategory.USER_FRICTION,
        raw_confidence=0.80,
        proposed_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
    )

    assert eval_result.customer_friction_paise >= 900
    assert eval_result.retry_penalty_paise >= 600
