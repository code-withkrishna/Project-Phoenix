"""Unit tests for ExecutionGuard."""

from datetime import datetime, timezone, timedelta
import uuid

import pytest

from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.schemas.ai import RecommendedAction, RecoveryPlan, RootCauseCategory, UrgencyLevel
from app.services.policy.models import DecisionType, MerchantPolicy, PolicyDecision
from app.services.recovery.execution_guard import ExecutionGuard


@pytest.fixture
def execution_guard() -> ExecutionGuard:
    return ExecutionGuard()


@pytest.fixture
def default_policy() -> MerchantPolicy:
    return MerchantPolicy()


@pytest.fixture
def valid_case() -> RecoveryCase:
    return RecoveryCase(
        id=uuid.uuid4(),
        payment_id="pay_GUARD_TEST_001",
        order_id="order_GUARD_TEST_001",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        status="PLAN_GENERATED",
        is_recovered=False,
    )


@pytest.fixture
def valid_plan() -> RecoveryPlan:
    return RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.95,
        diagnostic_summary="Valid summary",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Pay now",
    )



@pytest.fixture
def allow_decision() -> PolicyDecision:
    return PolicyDecision(
        decision=DecisionType.ALLOW,
        action="CREATE_PAYMENT_LINK",
        reason_codes=[],
    )


def test_guard_valid_execution(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Execution Guard allows valid input across all 14 checks."""
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is True
    assert result.reason is None


def test_guard_check_1_missing_case(
    execution_guard: ExecutionGuard,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #1: Missing case."""
    result = execution_guard.verify(
        case=None,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 1


def test_guard_check_2_invalid_state(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #2: Wrong case status (e.g. DETECTED)."""
    valid_case.status = "DETECTED"
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 2


def test_guard_check_3_already_recovered(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #3: Already recovered."""
    valid_case.is_recovered = True
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 3


def test_guard_check_6_decision_not_allow(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """Check #6: PolicyDecision is not ALLOW."""
    reject_decision = PolicyDecision(
        decision=DecisionType.REJECT,
        action="CREATE_PAYMENT_LINK",
        reason_codes=["RETRY_LIMIT_EXCEEDED"],
    )
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=reject_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 6


def test_guard_check_8_authoritative_amount_invalid(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #8: Authoritative case amount <= 0."""
    valid_case.amount = 0
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 8


def test_guard_check_9_currency_mismatch(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #9: Currency not INR."""
    valid_case.currency = "USD"
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_01",
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 9


def test_guard_check_11_active_duplicate_action(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #11: Active duplicate RecoveryAction already exists."""
    active_action = RecoveryAction(
        id=uuid.uuid4(),
        case_id=valid_case.id,
        reference_id="PHX_7F4A21C9_01",
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[active_action],
        reference_id="PHX_7F4A21C9_02",
        attempt_number=2,
    )
    assert result.allowed is False
    assert result.check_failed == 11


def test_guard_check_12_reference_id_too_long(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #12: Reference ID > 40 chars."""
    long_ref = "A" * 41
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id=long_ref,
        attempt_number=1,
    )
    assert result.allowed is False
    assert result.check_failed == 12


def test_guard_check_14_attempt_limit_exceeded(
    execution_guard: ExecutionGuard,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    allow_decision: PolicyDecision,
    default_policy: MerchantPolicy,
) -> None:
    """Check #14: Attempt limit exceeded."""
    result = execution_guard.verify(
        case=valid_case,
        plan=valid_plan,
        decision=allow_decision,
        policy=default_policy,
        prior_actions=[],
        reference_id="PHX_7F4A21C9_03",
        attempt_number=3,  # policy max is 2
    )
    assert result.allowed is False
    assert result.check_failed == 14
