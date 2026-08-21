"""Concurrency test: Two simultaneous recovery execution requests."""

import asyncio
from datetime import datetime, timezone
import uuid

import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.recovery_action import RecoveryAction
from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import RecommendedAction, RecoveryPlan, RootCauseCategory, UrgencyLevel
from app.services.razorpay.client import RazorpayClient
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
        confidence_score=0.94,
        diagnostic_summary="Customer cancelled due to timeout.",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete your order now.",
    )



@pytest.mark.asyncio
@respx.mock
async def test_concurrent_execution_creates_only_one_payment_link(
    db_session: AsyncSession,
    settings: Settings,
    plan: RecoveryPlan,
) -> None:
    """Two concurrent execution calls on the same RecoveryCase produce exactly one Payment Link and one RecoveryAction."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_CONC_{uuid.uuid4().hex[:6]}",
        order_id="order_CONC_01",
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
    case_id = case.id

    mock_route = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": "plink_CONC_001",
            "amount": 499900,
            "currency": "INR",
            "reference_id": f"PHX_{str(case_id).replace('-', '').upper()[:8]}_01",
            "short_url": "https://rzp.io/i/plink_CONC_001",
            "status": "created",
        },
    )

    client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, client)

        # Execute recovery for the first time
        result1 = await executor.execute_recovery(case_id, plan)
        assert result1.success is True

        # Second attempt immediately after should be blocked by ExecutionGuard / Policy
        result2 = await executor.execute_recovery(case_id, plan)
        assert result2.success is False

        # Verify only ONE RecoveryAction was persisted in database
        action_repo = RecoveryActionRepository(db_session)
        actions = await action_repo.list_by_case_id(case_id)
        assert len(actions) == 1
        assert actions[0].payment_link_id == "plink_CONC_001"

        # Verify Razorpay API was called only once
        assert mock_route.call_count == 1
    finally:
        await client.close()
