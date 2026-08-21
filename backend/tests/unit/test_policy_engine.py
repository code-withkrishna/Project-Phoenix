"""Unit tests for the Deterministic Policy Engine (POL-001 through POL-007)."""

from datetime import datetime, timezone, timedelta
import uuid

import pytest

from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.schemas.ai import RecommendedAction, RecoveryPlan, RootCauseCategory, UrgencyLevel

from app.services.policy.engine import PolicyEngine
from app.services.policy.models import DecisionType, MerchantPolicy


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


@pytest.fixture
def default_policy() -> MerchantPolicy:
    return MerchantPolicy(
        max_retry_attempts=2,
        min_link_expiry_minutes=15,
        max_link_expiry_minutes=1440,
        customer_cooldown_hours=2,
        min_amount_paise=10000,
        max_amount_paise=50000000,
    )


@pytest.fixture
def valid_case() -> RecoveryCase:
    return RecoveryCase(
        id=uuid.uuid4(),
        payment_id="pay_POL_TEST_001",
        order_id="order_POL_TEST_001",
        amount=499900,  # ₹4,999
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
        diagnostic_summary="Customer cancelled authentication due to timeout.",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete your order with instant 1-click payment.",
    )


def test_pol_001_whitelisted_actions(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-001: Allowed and disallowed actions."""
    # Allowed: DISPATCH_PAYMENT_LINK
    result = policy_engine.evaluate(valid_case, valid_plan, default_policy)
    assert result.decision == DecisionType.ALLOW
    assert result.action == "CREATE_PAYMENT_LINK"
    assert not result.violations

    # Allowed: DO_NOT_RECOVER
    do_not_recover_plan = valid_plan.model_copy(
        update={"recommended_action": RecommendedAction.DO_NOT_RECOVER}
    )
    result = policy_engine.evaluate(valid_case, do_not_recover_plan, default_policy)
    assert result.decision == DecisionType.ALLOW
    assert result.action == "DO_NOT_RECOVER"


def test_pol_001_forbidden_action(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    default_policy: MerchantPolicy,
) -> None:
    """POL-001: Forbidden action rejected immediately."""
    # Bypass enum validation to simulate arbitrary LLM string
    untrusted_plan = RecoveryPlan.model_construct(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.95,
        diagnostic_summary="Invalid transfer",
        recommended_action="UNAUTHORIZED_EXTERNAL_WIRE_TRANSFER",
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Pay now",
    )
    result = policy_engine.evaluate(valid_case, untrusted_plan, default_policy)
    assert result.decision == DecisionType.REJECT
    assert "ACTION_NOT_ALLOWED" in result.reason_codes


def test_pol_002_case_already_recovered(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-002: Rejects recovery if case is already recovered."""
    valid_case.is_recovered = True
    result = policy_engine.evaluate(valid_case, valid_plan, default_policy)
    assert result.decision == DecisionType.REJECT
    assert "RECOVERY_ALREADY_COMPLETED" in result.reason_codes


def test_pol_002_ineligible_case_state(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-002: Rejects recovery if case is in ineligible state or currency."""
    valid_case.status = "RESOLVED_EXTERNALLY"
    result = policy_engine.evaluate(valid_case, valid_plan, default_policy)
    assert result.decision == DecisionType.REJECT
    assert "INELIGIBLE_CASE_STATE" in result.reason_codes

    valid_case.status = "PLAN_GENERATED"
    valid_case.currency = "USD"
    result = policy_engine.evaluate(valid_case, valid_plan, default_policy)
    assert result.decision == DecisionType.REJECT
    assert "INELIGIBLE_CASE_STATE" in result.reason_codes


def test_pol_003_link_expiry_bounds(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-003: Enforces 15m <= expiry <= 1440m."""
    # Expiry too low (< 15 min)
    plan_low = valid_plan.model_copy(update={"link_expiry_minutes": 10})
    result_low = policy_engine.evaluate(valid_case, plan_low, default_policy)
    assert result_low.decision == DecisionType.REJECT
    assert "EXPIRY_BELOW_MINIMUM" in result_low.reason_codes

    # Expiry too high (> 1440 min)
    plan_high = valid_plan.model_copy(update={"link_expiry_minutes": 1500})
    result_high = policy_engine.evaluate(valid_case, plan_high, default_policy)
    assert result_high.decision == DecisionType.REJECT
    assert "EXPIRY_ABOVE_MAXIMUM" in result_high.reason_codes

    # Expiry exact boundaries: 15 and 1440
    plan_15 = valid_plan.model_copy(update={"link_expiry_minutes": 15})
    assert policy_engine.evaluate(valid_case, plan_15, default_policy).decision == DecisionType.ALLOW

    plan_1440 = valid_plan.model_copy(update={"link_expiry_minutes": 1440})
    assert policy_engine.evaluate(valid_case, plan_1440, default_policy).decision == DecisionType.ALLOW


def test_pol_004_amount_limits(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-004: Enforces ₹100 <= case.amount <= ₹500,000 using authoritative amount."""
    # Below ₹100 (10,000 paise)
    valid_case.amount = 5000  # ₹50
    result_low = policy_engine.evaluate(valid_case, valid_plan, default_policy)
    assert result_low.decision == DecisionType.REJECT
    assert "AMOUNT_BELOW_MINIMUM" in result_low.reason_codes

    # Above ₹500,000 (50,000,000 paise)
    valid_case.amount = 60000000  # ₹600,000
    result_high = policy_engine.evaluate(valid_case, valid_plan, default_policy)
    assert result_high.decision == DecisionType.REJECT
    assert "AMOUNT_LIMIT_EXCEEDED" in result_high.reason_codes


def test_pol_005_customer_cooldown(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-005: Enforces cooldown period between customer recovery actions."""
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

    # Action 1 hour ago (< 2h cooldown) -> REJECT
    recent_action = RecoveryAction(
        id=uuid.uuid4(),
        case_id=valid_case.id,
        reference_id="PHX_TEST_01",
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(hours=1),
        created_at=now - timedelta(hours=1),
    )
    result_cooldown = policy_engine.evaluate(
        valid_case, valid_plan, default_policy, prior_actions=[recent_action], current_time=now
    )
    assert result_cooldown.decision == DecisionType.REJECT
    assert "COOLDOWN_ACTIVE" in result_cooldown.reason_codes

    # Action 3 hours ago (> 2h cooldown) -> ALLOW (assuming under retry limit)
    old_action = RecoveryAction(
        id=uuid.uuid4(),
        case_id=valid_case.id,
        reference_id="PHX_TEST_01",
        amount=499900,
        currency="INR",
        status="EXPIRED",
        expires_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=3),
    )
    result_ok = policy_engine.evaluate(
        valid_case, valid_plan, default_policy, prior_actions=[old_action], current_time=now
    )
    assert result_ok.decision == DecisionType.ALLOW


def test_pol_006_retry_limits(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
    default_policy: MerchantPolicy,
) -> None:
    """POL-006: Enforces max retry attempts per case."""
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

    action1 = RecoveryAction(
        id=uuid.uuid4(),
        case_id=valid_case.id,
        reference_id="PHX_TEST_01",
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now - timedelta(hours=5),
        created_at=now - timedelta(hours=6),
    )
    action2 = RecoveryAction(
        id=uuid.uuid4(),
        case_id=valid_case.id,
        reference_id="PHX_TEST_02",
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=3),
    )

    # 2 active/issued actions >= max_retry_attempts (2) -> REJECT
    result = policy_engine.evaluate(
        valid_case, valid_plan, default_policy, prior_actions=[action1, action2], current_time=now
    )
    assert result.decision == DecisionType.REJECT
    assert "RETRY_LIMIT_EXCEEDED" in result.reason_codes


def test_pol_007_invalid_merchant_policy(
    policy_engine: PolicyEngine,
    valid_case: RecoveryCase,
    valid_plan: RecoveryPlan,
) -> None:
    """POL-007: Missing or invalid merchant policy fails closed."""
    # None policy -> FAIL CLOSED
    result_none = policy_engine.evaluate(valid_case, valid_plan, policy=None)
    assert result_none.decision == DecisionType.REJECT
    assert "INVALID_MERCHANT_POLICY" in result_none.reason_codes

    # Invalid policy with min_link_expiry < 15
    invalid_policy = MerchantPolicy(min_link_expiry_minutes=5)
    result_invalid = policy_engine.evaluate(valid_case, valid_plan, policy=invalid_policy)
    assert result_invalid.decision == DecisionType.REJECT
    assert "INVALID_MERCHANT_POLICY" in result_invalid.reason_codes
