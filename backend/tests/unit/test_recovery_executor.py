"""Unit tests for RecoveryExecutor and Timeout Reconciliation."""

from datetime import datetime, timezone, timedelta
import uuid

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import RecommendedAction, RecoveryPlan, RootCauseCategory, UrgencyLevel
from app.services.policy.engine import PolicyEngine
from app.services.policy.models import MerchantPolicy
from app.services.razorpay.client import RazorpayClient
from app.services.recovery.execution_guard import ExecutionGuard
from app.services.recovery.executor import RecoveryExecutor


@pytest.fixture
def settings() -> Settings:
    return Settings(
        razorpay_key_id="rzp_test_key123",
        razorpay_key_secret="rzp_test_secret123",
        razorpay_api_base_url="https://api.razorpay.com",
    )


@pytest.fixture
def plan() -> RecoveryPlan:
    return RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.92,
        diagnostic_summary="Payment failed due to user timeout.",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete your order now with 1-click checkout.",
    )



@pytest.mark.asyncio
@respx.mock
async def test_recovery_executor_happy_path(
    db_session: AsyncSession,
    settings: Settings,
    plan: RecoveryPlan,
) -> None:
    """Happy path: PLAN_GENERATED -> POLICY_APPROVED -> EXECUTING -> AWAITING_PAYMENT with 1 RecoveryAction."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_EXEC_HAPPY_{uuid.uuid4().hex[:6]}",
        order_id="order_EXEC_HAPPY_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="PLAN_GENERATED",
    )
    assert case is not None

    mock_post = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": "plink_EXEC_001",
            "amount": 499900,
            "currency": "INR",
            "reference_id": f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01",
            "short_url": "https://rzp.io/i/plink_EXEC_001",
            "status": "created",
        },
    )

    client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, client)
        result = await executor.execute_recovery(case.id, plan)

        assert result.success is True
        assert result.case.status == "AWAITING_PAYMENT"
        assert result.action is not None
        assert result.action.status == "ISSUED"
        assert result.action.payment_link_id == "plink_EXEC_001"
        assert result.action.payment_link_url == "https://rzp.io/i/plink_EXEC_001"
        assert mock_post.called

        # Verify audit trail
        audits = await case_repo.get_audit_trail(case.id)
        triggers = [a.trigger for a in audits]
        assert "POLICY_EVALUATED" in triggers
        assert "POLICY_APPROVED" in triggers
        assert "RECOVERY_ACTION_CREATED" in triggers
        assert "RAZORPAY_PAYMENT_LINK_CREATED" in triggers
        assert "PAYMENT_LINK_ISSUED" in triggers
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_recovery_executor_timeout_reconciliation_found(
    db_session: AsyncSession,
    settings: Settings,
    plan: RecoveryPlan,
) -> None:
    """Mandatory Timeout Reconciliation Test: POST timeout -> GET by reference found -> AWAITING_PAYMENT with 1 action."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_EXEC_TIMEOUT_{uuid.uuid4().hex[:6]}",
        order_id="order_EXEC_TIMEOUT_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="PLAN_GENERATED",
    )
    assert case is not None
    expected_ref = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"

    # POST times out
    respx.post("https://api.razorpay.com/v1/payment_links").mock(
        side_effect=httpx.TimeoutException("Read timeout on POST /v1/payment_links")
    )

    # GET by reference succeeds and finds the link created on Razorpay
    respx.get("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "count": 1,
            "payment_links": [
                {
                    "id": "plink_RECONCILED_001",
                    "amount": 499900,
                    "currency": "INR",
                    "reference_id": expected_ref,
                    "short_url": "https://rzp.io/i/plink_RECONCILED_001",
                    "status": "created",
                }
            ],
        },
    )

    client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, client)
        result = await executor.execute_recovery(case.id, plan)

        assert result.success is True
        assert result.case.status == "AWAITING_PAYMENT"
        assert result.action is not None
        assert result.action.status == "ISSUED"
        assert result.action.payment_link_id == "plink_RECONCILED_001"
        assert result.action.reference_id == expected_ref

        # Check that exactly ONE RecoveryAction exists
        action_repo = RecoveryActionRepository(db_session)
        actions = await action_repo.list_by_case_id(case.id)
        assert len(actions) == 1

        # Check audit trail recorded reconciliation
        audits = await case_repo.get_audit_trail(case.id)
        triggers = [a.trigger for a in audits]
        assert "RAZORPAY_PAYMENT_LINK_RECONCILED" in triggers
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_recovery_executor_timeout_reconciliation_not_found(
    db_session: AsyncSession,
    settings: Settings,
    plan: RecoveryPlan,
) -> None:
    """Timeout Reconciliation: POST times out and GET by reference finds nothing -> FAILED."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_EXEC_TIMEOUT_FAIL_{uuid.uuid4().hex[:6]}",
        order_id="order_EXEC_TIMEOUT_FAIL_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="PLAN_GENERATED",
    )
    assert case is not None

    respx.post("https://api.razorpay.com/v1/payment_links").mock(
        side_effect=httpx.TimeoutException("Read timeout on POST")
    )
    respx.get("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={"count": 0, "payment_links": []},
    )

    client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, client)
        result = await executor.execute_recovery(case.id, plan)

        assert result.success is False
        assert result.case.status == "FAILED"
        assert result.action is not None
        assert result.action.status == "FAILED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_recovery_executor_policy_rejected(
    db_session: AsyncSession,
    settings: Settings,
    plan: RecoveryPlan,
) -> None:
    """Case is rejected by policy engine -> transitions to POLICY_REJECTED without Razorpay call."""
    case_repo = RecoveryCaseRepository(db_session)
    # Case amount below minimum
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_EXEC_POL_REJ_{uuid.uuid4().hex[:6]}",
        order_id="order_EXEC_POL_REJ_01",
        amount=500,  # ₹5 < ₹100
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="PLAN_GENERATED",
    )
    assert case is not None

    client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, client)
        result = await executor.execute_recovery(case.id, plan)

        assert result.success is False
        assert result.case.status == "POLICY_REJECTED"
        assert result.action is None
    finally:
        await client.close()
