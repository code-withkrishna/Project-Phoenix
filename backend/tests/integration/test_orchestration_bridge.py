"""Comprehensive Integration & Unit Tests for the Project Phoenix Orchestration Bridge.

Covers all 16 mission test requirements:
TEST 1: DETECTED recovery case triggers orchestration.
TEST 2: Context Engine is invoked.
TEST 3: AI Planner produces a plan.
TEST 4: Policy Engine evaluates the plan.
TEST 5: Execution Guard authorizes valid execution.
TEST 6: Payment Link execution is invoked through the existing executor.
TEST 7: Successful execution moves the case into the correct awaiting-payment state.
TEST 8: AI failure is fail-closed.
TEST 9: Policy rejection prevents execution.
TEST 10: Execution Guard rejection prevents execution.
TEST 11: Duplicate webhook/event does not create duplicate recovery action.
TEST 12: Duplicate orchestration attempt is idempotent.
TEST 13: Razorpay execution timeout uses existing reconciliation behavior.
TEST 14: payment_link.paid results in recovery completion.
TEST 15: Duplicate payment_link.paid remains idempotent.
TEST 16: Existing Wave 1–3 regression suite remains green.
"""

import json
from datetime import datetime, timezone
import uuid

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.ai_diagnosis import AIDiagnosis
from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.repositories.ai_diagnoses import AIDiagnosisRepository
from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import (
    AIDiagnosisMetadata,
    CustomerHistoryContext,
    DiagnosticContext,
    MerchantConstraintsContext,
    RecommendedAction,
    RecoveryPlan,
    RootCauseCategory,
    UrgencyLevel,
)
from app.services.ai.base import AIProvider
from app.services.ai.mock import MockAIProvider
from app.services.policy.engine import PolicyEngine
from app.services.policy.models import DecisionType, MerchantPolicy, PolicyDecision
from app.services.razorpay.client import RazorpayClient
from app.services.recovery.context_engine import ContextEngine
from app.services.recovery.execution_guard import ExecutionGuard
from app.services.recovery.executor import RecoveryExecutor
from app.services.recovery.orchestrator import OrchestrationResult, RecoveryOrchestrator
from app.services.recovery.planner import AIRecoveryPlanner
from app.services.recovery.reference import generate_reference_id
from app.services.webhooks.dispatcher import WebhookDispatcher
from tests.fixtures.razorpay import (
    payment_failed_payload,
    payment_link_paid_payload,
    razorpay_payment_api_response,
    webhook_headers,
)


class FailingAIProvider(AIProvider):
    """Provider that simulates unrecoverable AI service failure."""

    async def generate_plan(
        self,
        context: DiagnosticContext,
        correction_prompt: str | None = None,
    ) -> tuple[str, AIDiagnosisMetadata]:
        raise RuntimeError("AI Provider network connection timeout (simulated)")


class MalformedAIProvider(AIProvider):
    """Provider that always returns malformed unparseable output."""

    async def generate_plan(
        self,
        context: DiagnosticContext,
        correction_prompt: str | None = None,
    ) -> tuple[str, AIDiagnosisMetadata]:
        return "NOT_JSON_AT_ALL_THIS_IS_CORRUPT", AIDiagnosisMetadata(
            model_name="mock-corrupt",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=45,
            raw_response={"raw": "corrupt"},
        )



@pytest.mark.asyncio
@respx.mock
async def test_1_detected_case_triggers_orchestration_end_to_end(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST 1: Ingesting payment.failed triggers autonomous orchestration to AWAITING_PAYMENT."""
    monkeypatch.setenv("AUTO_ORCHESTRATE", "true")
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    payment_id = f"pay_TEST1_{uuid.uuid4().hex[:6]}"
    order_id = "order_TEST1_001"
    event_id = f"event_TEST1_{uuid.uuid4().hex[:6]}"
    plink_id = f"plink_TEST1_{uuid.uuid4().hex[:6]}"


    # Mock Razorpay APIs
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").respond(
        status_code=200,
        json=razorpay_payment_api_response(payment_id=payment_id, status="failed", order_id=order_id),
    )
    mock_plink = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 499900,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    # Ingest webhook
    failed_payload = payment_failed_payload(payment_id=payment_id, order_id=order_id, event_id=event_id)
    failed_payload.pop("_test_fixture_event_id", None)
    raw_failed = json.dumps(failed_payload, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_failed, event_id=event_id, secret=settings.razorpay_webhook_secret)

    resp = await client.post("/api/v1/webhooks/razorpay", content=raw_failed, headers=headers)
    assert resp.status_code == 200

    # Dispatch background processing with auto_orchestrate=True
    dispatcher = WebhookDispatcher(
        db_session,
        settings,
        auto_orchestrate=True,
    )
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    case_repo = RecoveryCaseRepository(db_session)
    case = await case_repo.get_by_payment_id(payment_id)
    assert case is not None
    assert case.status == "AWAITING_PAYMENT"
    assert mock_plink.called

    # Verify action was created and issued
    action_repo = RecoveryActionRepository(db_session)
    actions = await action_repo.list_by_case_id(case.id)
    assert len(actions) == 1
    assert actions[0].status == "ISSUED"
    assert actions[0].payment_link_id == plink_id


@pytest.mark.asyncio
async def test_2_context_engine_invoked(
    db_session: AsyncSession,
) -> None:
    """TEST 2: Context Engine extracts features, aggregates customer history, and strips PII."""
    case_repo = RecoveryCaseRepository(db_session)
    payment_id = f"pay_TEST2_{uuid.uuid4().hex[:6]}"
    case, _ = await case_repo.create_if_absent(
        payment_id=payment_id,
        order_id="order_TEST2",
        amount=250000,
        currency="INR",
        customer_email="customer@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment was cancelled by the user on UPI app",
            "error_source": "customer",
            "error_step": "payment_authentication",
            "error_reason": "payment_cancelled",
            "method": "upi",
            "vpa": "customer_secret_name@oksbi",
            "bank": "HDFC",
            "email": "customer@example.com",
            "phone": "+919876543210",
        },
        status="DETECTED",
    )
    assert case is not None

    context_engine = ContextEngine(db_session)
    ctx = await context_engine.build_context(case)

    assert ctx.payment_id == payment_id
    assert ctx.amount_paise == 250000
    assert ctx.currency == "INR"
    assert ctx.error_code == "BAD_REQUEST_ERROR"
    # PII sanitization: email & phone are not in payment_details, vpa handle domain masked
    assert "email" not in ctx.payment_details
    assert "phone" not in ctx.payment_details
    assert ctx.payment_details.get("vpa_handle") == "***@oksbi"
    assert ctx.customer_history is not None


@pytest.mark.asyncio
async def test_3_ai_planner_produces_plan(
    db_session: AsyncSession,
) -> None:
    """TEST 3: AI Recovery Planner produces structured RecoveryPlan and persists AIDiagnosis."""
    case_repo = RecoveryCaseRepository(db_session)
    payment_id = f"pay_TEST3_{uuid.uuid4().hex[:6]}"
    case, _ = await case_repo.create_if_absent(
        payment_id=payment_id,
        order_id="order_TEST3",
        amount=199900,
        currency="INR",
        customer_email="test3@example.com",
        customer_phone="+919876543211",
        failure_code="GATEWAY_ERROR",
        failure_reason="bank_timeout",
        failure_telemetry={"method": "netbanking", "bank": "SBIN"},
        status="DETECTED",
    )
    assert case is not None

    ai_provider = MockAIProvider(model_name="mock-test-v1")
    planner = AIRecoveryPlanner(db_session, ai_provider=ai_provider)
    result = await planner.plan_recovery(case.id)

    assert result.success is True
    assert result.plan is not None
    assert isinstance(result.plan, RecoveryPlan)
    assert result.plan.recommended_action in {RecommendedAction.DISPATCH_PAYMENT_LINK, RecommendedAction.DO_NOT_RECOVER}
    assert result.plan.link_expiry_minutes >= 15
    assert result.case.status == "PLAN_GENERATED"

    # Verify persisted diagnosis
    diag_repo = AIDiagnosisRepository(db_session)
    diags = await diag_repo.get_by_case_id(case.id)
    assert len(diags) == 1
    assert diags[0].model_name == "mock-test-v1"



@pytest.mark.asyncio
async def test_4_policy_engine_evaluates_plan(
    db_session: AsyncSession,
) -> None:
    """TEST 4: Policy Engine evaluates plan and applies invariants."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST4_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST4",
        amount=100000,
        currency="INR",
        customer_email="test4@example.com",
        customer_phone="+919876543212",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="mpin_timeout",
        failure_telemetry={"method": "upi"},
        status="PLAN_GENERATED",
    )
    assert case is not None

    plan = RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.95,
        diagnostic_summary="MPIN timeout on UPI app",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete your payment here",
    )

    policy_engine = PolicyEngine()
    policy = MerchantPolicy(min_link_expiry_minutes=15, max_link_expiry_minutes=1440)
    decision = policy_engine.evaluate(case=case, plan=plan, policy=policy)

    assert decision.decision == DecisionType.ALLOW
    assert decision.action == "CREATE_PAYMENT_LINK"
    assert len(decision.violations) == 0


@pytest.mark.asyncio
async def test_5_execution_guard_authorizes_valid_execution(
    db_session: AsyncSession,
) -> None:
    """TEST 5: Execution Guard verifies all 14 mandatory invariants."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST5_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST5",
        amount=100000,
        currency="INR",
        customer_email="test5@example.com",
        customer_phone="+919876543213",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="mpin_timeout",
        failure_telemetry={"method": "upi"},
        status="POLICY_APPROVED",
    )
    assert case is not None

    plan = RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.9,
        diagnostic_summary="MPIN timeout",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=30,
        customer_facing_message="Complete payment",
    )
    decision = PolicyDecision(
        decision=DecisionType.ALLOW,
        action="CREATE_PAYMENT_LINK",
        reason_codes=[],
        constraints={"expiry_minutes": 30},
    )
    guard = ExecutionGuard()
    result = guard.verify(
        case=case,
        plan=plan,
        decision=decision,
        policy=MerchantPolicy(),
        prior_actions=[],
        reference_id=generate_reference_id(case.id, sequence=1),
        attempt_number=1,
    )
    assert result.allowed is True


@pytest.mark.asyncio
@respx.mock
async def test_6_payment_link_execution_invoked(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 6: Payment Link execution is invoked through the existing executor with correct payload."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST6_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST6",
        amount=150000,
        currency="INR",
        customer_email="test6@example.com",
        customer_phone="+919876543214",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="user_dropped",
        failure_telemetry={"method": "card"},
        status="PLAN_GENERATED",
    )
    assert case is not None

    plink_id = f"plink_TEST6_{uuid.uuid4().hex[:6]}"
    mock_post = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 150000,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    plan = RecoveryPlan(
        root_cause_category=RootCauseCategory.USER_FRICTION,
        confidence_score=0.92,
        diagnostic_summary="Card OTP dropped",
        recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
        urgency=UrgencyLevel.HIGH,
        link_expiry_minutes=25,
        customer_facing_message="Complete your payment here",
    )

    rzp_client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, rzp_client)
        exec_result = await executor.execute_recovery(case.id, plan)

        assert exec_result.success is True
        assert mock_post.called
        # Check payload sent to Razorpay
        req = mock_post.calls.last.request
        req_body = json.loads(req.content)
        assert req_body["amount"] == 150000
        assert req_body["currency"] == "INR"
        assert req_body["customer"]["email"] == "test6@example.com"
        assert req_body["notes"]["phoenix_case_id"] == str(case.id)
    finally:
        await rzp_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_7_successful_execution_moves_case_to_awaiting_payment(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 7: Successful execution transitions case to AWAITING_PAYMENT."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST7_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST7",
        amount=300000,
        currency="INR",
        customer_email="test7@example.com",
        customer_phone="+919876543215",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="user_cancelled",
        failure_telemetry={"method": "upi"},
        status="DETECTED",
    )
    assert case is not None

    plink_id = f"plink_TEST7_{uuid.uuid4().hex[:6]}"
    respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 300000,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    rzp_client = RazorpayClient(settings)
    try:
        orchestrator = RecoveryOrchestrator(
            db_session,
            razorpay_client=rzp_client,
            ai_provider=MockAIProvider(),
        )
        orch_result = await orchestrator.orchestrate_case(case.id)

        assert orch_result.success is True
        assert orch_result.case.status == "AWAITING_PAYMENT"
        assert orch_result.execution_result is not None
        assert orch_result.execution_result.action is not None
        assert orch_result.execution_result.action.status == "ISSUED"
    finally:
        await rzp_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_8_ai_failure_is_fail_closed(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 8: AI failure fails closed -> case transitions to ESCALATED with 0 Razorpay calls."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST8_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST8",
        amount=100000,
        currency="INR",
        customer_email="test8@example.com",
        customer_phone="+919876543216",
        failure_code="GATEWAY_ERROR",
        failure_reason="bank_timeout",
        failure_telemetry={},
        status="DETECTED",
    )
    assert case is not None

    mock_post = respx.post("https://api.razorpay.com/v1/payment_links").respond(status_code=200)

    rzp_client = RazorpayClient(settings)
    try:
        orchestrator = RecoveryOrchestrator(
            db_session,
            razorpay_client=rzp_client,
            ai_provider=FailingAIProvider(),
        )
        orch_result = await orchestrator.orchestrate_case(case.id)

        assert orch_result.success is False
        assert orch_result.stage == "PLANNING"
        assert orch_result.case.status == "ESCALATED"
        # Zero Razorpay calls made
        assert not mock_post.called
    finally:
        await rzp_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_9_policy_rejection_prevents_execution(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 9: Policy rejection transitions case to POLICY_REJECTED with zero Razorpay calls."""
    case_repo = RecoveryCaseRepository(db_session)
    # Case amount below minimum 10000 paise (Rs 100)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST9_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST9",
        amount=5000,  # Rs 50 -> violates POL-004
        currency="INR",
        customer_email="test9@example.com",
        customer_phone="+919876543217",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="user_cancelled",
        failure_telemetry={},
        status="DETECTED",
    )
    assert case is not None

    mock_post = respx.post("https://api.razorpay.com/v1/payment_links").respond(status_code=200)

    rzp_client = RazorpayClient(settings)
    try:
        orchestrator = RecoveryOrchestrator(
            db_session,
            razorpay_client=rzp_client,
            ai_provider=MockAIProvider(),
        )
        orch_result = await orchestrator.orchestrate_case(
            case.id,
            policy=MerchantPolicy(min_amount_paise=10000),
        )

        assert orch_result.success is False
        assert orch_result.stage == "EXECUTION"
        assert orch_result.case.status == "POLICY_REJECTED"
        assert not mock_post.called
    finally:
        await rzp_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_10_execution_guard_rejection_prevents_execution(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 10: Execution Guard rejection transitions case to ESCALATED with zero Razorpay calls."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST10_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST10",
        amount=200000,
        currency="INR",
        customer_email="test10@example.com",
        customer_phone="+919876543218",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="user_cancelled",
        failure_telemetry={},
        status="DETECTED",
    )
    assert case is not None

    # Pre-create an active action to trigger Check #11 violation
    await action_repo.create(
        case_id=case.id,
        reference_id=f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01",
        amount=case.amount,
        currency=case.currency,
        expires_at=datetime.now(timezone.utc),
        status="ISSUED",
    )

    mock_post = respx.post("https://api.razorpay.com/v1/payment_links").respond(status_code=200)

    rzp_client = RazorpayClient(settings)
    try:
        orchestrator = RecoveryOrchestrator(
            db_session,
            razorpay_client=rzp_client,
            ai_provider=MockAIProvider(),
        )
        orch_result = await orchestrator.orchestrate_case(
            case.id,
            policy=MerchantPolicy(customer_cooldown_hours=0),
        )

        assert orch_result.success is False
        assert orch_result.stage == "EXECUTION"
        assert orch_result.case.status == "ESCALATED"
        assert not mock_post.called
    finally:
        await rzp_client.close()



@pytest.mark.asyncio
@respx.mock
async def test_11_duplicate_webhook_does_not_create_duplicate_recovery_action(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST 11: Ingesting duplicate payment.failed webhooks creates exactly 1 action."""
    monkeypatch.setenv("AUTO_ORCHESTRATE", "true")
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    payment_id = f"pay_TEST11_{uuid.uuid4().hex[:6]}"
    order_id = "order_TEST11"
    event_id = f"event_TEST11_{uuid.uuid4().hex[:6]}"
    plink_id = f"plink_TEST11_{uuid.uuid4().hex[:6]}"


    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").respond(
        status_code=200,
        json=razorpay_payment_api_response(payment_id=payment_id, status="failed", order_id=order_id),
    )
    respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 499900,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    failed_payload = payment_failed_payload(payment_id=payment_id, order_id=order_id, event_id=event_id)
    failed_payload.pop("_test_fixture_event_id", None)
    raw_failed = json.dumps(failed_payload, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_failed, event_id=event_id, secret=settings.razorpay_webhook_secret)

    # First delivery
    resp1 = await client.post("/api/v1/webhooks/razorpay", content=raw_failed, headers=headers)
    assert resp1.status_code == 200

    dispatcher = WebhookDispatcher(db_session, settings, auto_orchestrate=True)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    # Second duplicate delivery
    resp2 = await client.post("/api/v1/webhooks/razorpay", content=raw_failed, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["action_taken"] == "DEDUPLICATED"

    case_repo = RecoveryCaseRepository(db_session)
    case = await case_repo.get_by_payment_id(payment_id)
    assert case is not None

    action_repo = RecoveryActionRepository(db_session)
    actions = await action_repo.list_by_case_id(case.id)
    assert len(actions) == 1


@pytest.mark.asyncio
@respx.mock
async def test_12_duplicate_orchestration_attempt_is_idempotent(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 12: Re-invoking orchestrator on an already active case is idempotent and safe."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST12_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST12",
        amount=120000,
        currency="INR",
        customer_email="test12@example.com",
        customer_phone="+919876543220",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="user_cancelled",
        failure_telemetry={},
        status="DETECTED",
    )
    assert case is not None

    plink_id = f"plink_TEST12_{uuid.uuid4().hex[:6]}"
    mock_post = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 120000,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    rzp_client = RazorpayClient(settings)
    try:
        orchestrator = RecoveryOrchestrator(
            db_session,
            razorpay_client=rzp_client,
            ai_provider=MockAIProvider(),
        )
        # Attempt 1 -> Moves to AWAITING_PAYMENT
        res1 = await orchestrator.orchestrate_case(case.id)
        assert res1.success is True
        assert res1.case.status == "AWAITING_PAYMENT"
        assert mock_post.call_count == 1

        # Attempt 2 -> Idempotently skipped (ALREADY_ACTIVE)
        res2 = await orchestrator.orchestrate_case(case.id)
        assert res2.success is True
        assert res2.stage == "ALREADY_ACTIVE"
        assert mock_post.call_count == 1  # No second external API call
    finally:
        await rzp_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_13_razorpay_execution_timeout_reconciliation(
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """TEST 13: Razorpay 408 timeout triggers timeout reconciliation by reference_id."""
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id=f"pay_TEST13_{uuid.uuid4().hex[:6]}",
        order_id="order_TEST13",
        amount=180000,
        currency="INR",
        customer_email="test13@example.com",
        customer_phone="+919876543221",
        failure_code="GATEWAY_ERROR",
        failure_reason="network_timeout",
        failure_telemetry={},
        status="DETECTED",
    )
    assert case is not None

    plink_id = f"plink_TEST13_{uuid.uuid4().hex[:6]}"
    expected_ref = generate_reference_id(case.id, sequence=1)

    # 1. POST /v1/payment_links returns 408 Timeout
    respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=408,
        json={"error": {"code": "GATEWAY_TIMEOUT", "description": "Request timed out"}},
    )

    # 2. Timeout reconciliation query GET /v1/payment_links?reference_id=... returns created link
    respx.get("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "payment_links": [
                {
                    "id": plink_id,
                    "reference_id": expected_ref,
                    "amount": 180000,
                    "currency": "INR",
                    "short_url": f"https://rzp.io/i/{plink_id}",
                    "status": "created",
                }
            ]
        },
    )


    rzp_client = RazorpayClient(settings)
    try:
        orchestrator = RecoveryOrchestrator(
            db_session,
            razorpay_client=rzp_client,
            ai_provider=MockAIProvider(),
        )
        result = await orchestrator.orchestrate_case(case.id)

        assert result.success is True
        assert result.case.status == "AWAITING_PAYMENT"
        assert result.execution_result is not None
        assert result.execution_result.action is not None
        assert result.execution_result.action.status == "ISSUED"
        assert result.execution_result.action.payment_link_id == plink_id
    finally:
        await rzp_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_14_payment_link_paid_completes_recovery(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST 14: payment_link.paid webhook resolves case to RECOVERED and action to PAID."""
    monkeypatch.setenv("AUTO_ORCHESTRATE", "true")
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    payment_id = f"pay_TEST14_{uuid.uuid4().hex[:6]}"
    order_id = "order_TEST14"
    event_id_failed = f"event_TEST14_FAIL_{uuid.uuid4().hex[:6]}"
    plink_id = f"plink_TEST14_{uuid.uuid4().hex[:6]}"


    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").respond(
        status_code=200,
        json=razorpay_payment_api_response(payment_id=payment_id, status="failed", order_id=order_id),
    )
    respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 499900,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    # 1. Ingest failed webhook & orchestrate to AWAITING_PAYMENT
    failed_payload = payment_failed_payload(payment_id=payment_id, order_id=order_id, event_id=event_id_failed)
    failed_payload.pop("_test_fixture_event_id", None)
    raw_failed = json.dumps(failed_payload, separators=(",", ":")).encode("utf-8")
    headers_failed = webhook_headers(raw_failed, event_id=event_id_failed, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_failed, headers=headers_failed)

    dispatcher = WebhookDispatcher(db_session, settings, auto_orchestrate=True)
    try:
        await dispatcher.process_event(event_id_failed)
    finally:
        await dispatcher.close()

    case_repo = RecoveryCaseRepository(db_session)
    case = await case_repo.get_by_payment_id(payment_id)
    assert case is not None
    assert case.status == "AWAITING_PAYMENT"

    action_repo = RecoveryActionRepository(db_session)
    actions = await action_repo.list_by_case_id(case.id)
    assert len(actions) == 1
    action = actions[0]

    # 2. Customer pays link -> payment_link.paid arrives
    event_id_paid = f"event_TEST14_PAID_{uuid.uuid4().hex[:6]}"
    paid_payload = payment_link_paid_payload(
        event_id=event_id_paid,
        payment_link_id=plink_id,
        reference_id=action.reference_id,
        payment_id=f"pay_CAPTURED_{uuid.uuid4().hex[:6]}",
        amount=case.amount,
        amount_paid=case.amount,
        payment_status="captured",
    )
    paid_payload.pop("_test_fixture_event_id", None)
    raw_paid = json.dumps(paid_payload, separators=(",", ":")).encode("utf-8")
    headers_paid = webhook_headers(raw_paid, event_id=event_id_paid, secret=settings.razorpay_webhook_secret)

    resp_paid = await client.post("/api/v1/webhooks/razorpay", content=raw_paid, headers=headers_paid)
    assert resp_paid.status_code == 200

    dispatcher_paid = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher_paid.process_event(event_id_paid)
    finally:
        await dispatcher_paid.close()

    await db_session.refresh(case)
    await db_session.refresh(action)

    assert case.status == "RECOVERED"
    assert case.is_recovered is True
    assert case.recovered_amount == case.amount
    assert action.status == "PAID"


@pytest.mark.asyncio
@respx.mock
async def test_15_duplicate_payment_link_paid_is_idempotent(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TEST 15: Duplicate payment_link.paid deliveries remain idempotent on terminal RECOVERED state."""
    monkeypatch.setenv("AUTO_ORCHESTRATE", "true")
    from app.core.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    payment_id = f"pay_TEST15_{uuid.uuid4().hex[:6]}"
    order_id = "order_TEST15"
    event_id_failed = f"event_TEST15_FAIL_{uuid.uuid4().hex[:6]}"
    plink_id = f"plink_TEST15_{uuid.uuid4().hex[:6]}"


    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").respond(
        status_code=200,
        json=razorpay_payment_api_response(payment_id=payment_id, status="failed", order_id=order_id),
    )
    respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": 499900,
            "currency": "INR",
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    failed_payload = payment_failed_payload(payment_id=payment_id, order_id=order_id, event_id=event_id_failed)
    failed_payload.pop("_test_fixture_event_id", None)
    raw_failed = json.dumps(failed_payload, separators=(",", ":")).encode("utf-8")
    headers_failed = webhook_headers(raw_failed, event_id=event_id_failed, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_failed, headers=headers_failed)

    dispatcher = WebhookDispatcher(db_session, settings, auto_orchestrate=True)
    try:
        await dispatcher.process_event(event_id_failed)
    finally:
        await dispatcher.close()

    case_repo = RecoveryCaseRepository(db_session)
    case = await case_repo.get_by_payment_id(payment_id)
    assert case is not None

    action_repo = RecoveryActionRepository(db_session)
    actions = await action_repo.list_by_case_id(case.id)
    action = actions[0]

    # First payment_link.paid event
    event_id_paid1 = f"event_TEST15_PAID1_{uuid.uuid4().hex[:6]}"
    paid_payload1 = payment_link_paid_payload(
        event_id=event_id_paid1,
        payment_link_id=plink_id,
        reference_id=action.reference_id,
        payment_id=f"pay_CAPTURED1_{uuid.uuid4().hex[:6]}",
        amount=case.amount,
        amount_paid=case.amount,
        payment_status="captured",
    )
    paid_payload1.pop("_test_fixture_event_id", None)
    raw_paid1 = json.dumps(paid_payload1, separators=(",", ":")).encode("utf-8")
    headers_paid1 = webhook_headers(raw_paid1, event_id=event_id_paid1, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_paid1, headers=headers_paid1)

    dispatcher_paid = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher_paid.process_event(event_id_paid1)
    finally:
        await dispatcher_paid.close()

    await db_session.refresh(case)
    assert case.status == "RECOVERED"

    # Second duplicate payment_link.paid event (different event_id or retry)
    event_id_paid2 = f"event_TEST15_PAID2_{uuid.uuid4().hex[:6]}"
    paid_payload2 = payment_link_paid_payload(
        event_id=event_id_paid2,
        payment_link_id=plink_id,
        reference_id=action.reference_id,
        payment_id=f"pay_CAPTURED2_{uuid.uuid4().hex[:6]}",
        amount=case.amount,
        amount_paid=case.amount,
        payment_status="captured",
    )
    paid_payload2.pop("_test_fixture_event_id", None)
    raw_paid2 = json.dumps(paid_payload2, separators=(",", ":")).encode("utf-8")
    headers_paid2 = webhook_headers(raw_paid2, event_id=event_id_paid2, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_paid2, headers=headers_paid2)

    dispatcher_paid2 = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher_paid2.process_event(event_id_paid2)
    finally:
        await dispatcher_paid2.close()

    await db_session.refresh(case)
    assert case.status == "RECOVERED"
    assert case.is_recovered is True
