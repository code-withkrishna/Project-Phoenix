"""Full mocked end-to-end recovery pipeline test."""

import json
from datetime import datetime, timezone
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
from app.services.ai.mock import MockAIProvider
from app.services.razorpay.client import RazorpayClient
from app.services.recovery.executor import RecoveryExecutor
from app.services.recovery.planner import AIRecoveryPlanner
from app.services.webhooks.dispatcher import WebhookDispatcher

from tests.fixtures.razorpay import (
    payment_failed_payload,
    payment_link_paid_payload,
    razorpay_payment_api_response,
    webhook_headers,
)


@pytest.mark.asyncio
@respx.mock
async def test_full_mocked_recovery_pipeline(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """Full End-to-End Pipeline: payment.failed -> AI Diagnosis -> Policy Engine -> Execution Guard -> Payment Link -> payment_link.paid -> RECOVERED."""
    payment_id = f"pay_FULL_E2E_{uuid.uuid4().hex[:6]}"
    order_id = "order_FULL_E2E_01"
    event_id_failed = f"event_FULL_E2E_FAILED_{uuid.uuid4().hex[:6]}"

    # Mock Razorpay GET /v1/payments/{payment_id} for initial reconciliation
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").respond(
        status_code=200,
        json=razorpay_payment_api_response(payment_id=payment_id, status="failed", order_id=order_id),
    )

    # 1. Ingest payment.failed webhook
    failed_payload = payment_failed_payload(payment_id=payment_id, order_id=order_id, event_id=event_id_failed)
    failed_payload.pop("_test_fixture_event_id", None)
    raw_failed = json.dumps(failed_payload, separators=(",", ":")).encode("utf-8")
    headers_failed = webhook_headers(raw_failed, event_id=event_id_failed, secret=settings.razorpay_webhook_secret)

    resp_failed = await client.post("/api/v1/webhooks/razorpay", content=raw_failed, headers=headers_failed)
    assert resp_failed.status_code == 200

    # 2. Dispatch webhook processing -> creates RecoveryCase in DETECTED
    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id_failed)
    finally:
        await dispatcher.close()

    case_repo = RecoveryCaseRepository(db_session)
    case = await case_repo.get_by_payment_id(payment_id)
    assert case is not None
    assert case.status == "DETECTED"

    # 3. Wave 2 AI Diagnosis & Planning -> PLAN_GENERATED
    ai_provider = MockAIProvider(model_name="mock-gemini-2.5-flash")
    planner = AIRecoveryPlanner(db_session, ai_provider=ai_provider)
    planner_result = await planner.plan_recovery(case.id)

    assert planner_result.success is True
    assert planner_result.case.status == "PLAN_GENERATED"
    assert planner_result.plan is not None

    # 4. Wave 3 Policy Engine + Execution Guard + Razorpay Payment Link Dispatch
    expected_ref = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = f"plink_FULL_E2E_{uuid.uuid4().hex[:6]}"

    mock_post_link = respx.post("https://api.razorpay.com/v1/payment_links").respond(
        status_code=200,
        json={
            "id": plink_id,
            "amount": case.amount,
            "currency": case.currency,
            "reference_id": expected_ref,
            "short_url": f"https://rzp.io/i/{plink_id}",
            "status": "created",
        },
    )

    rzp_client = RazorpayClient(settings)
    try:
        executor = RecoveryExecutor(db_session, rzp_client)
        exec_result = await executor.execute_recovery(case.id, planner_result.plan)

        assert exec_result.success is True
        assert exec_result.case.status == "AWAITING_PAYMENT"
        assert exec_result.action is not None
        assert exec_result.action.status == "ISSUED"
        assert exec_result.action.payment_link_id == plink_id
        assert exec_result.action.reference_id == expected_ref
        assert mock_post_link.called
    finally:
        await rzp_client.close()

    # 5. Customer completes payment -> payment_link.paid webhook arrives
    event_id_paid = f"event_FULL_E2E_PAID_{uuid.uuid4().hex[:6]}"
    paid_payload = payment_link_paid_payload(
        event_id=event_id_paid,
        payment_link_id=plink_id,
        reference_id=expected_ref,
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

    # 6. Dispatch payment_link.paid webhook processing
    dispatcher_paid = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher_paid.process_event(event_id_paid)
    finally:
        await dispatcher_paid.close()

    # 7. Assert terminal success: RecoveryCase -> RECOVERED, RecoveryAction -> PAID
    await db_session.refresh(case)
    await db_session.refresh(exec_result.action)

    assert exec_result.action.status == "PAID"
    assert case.status == "RECOVERED"
    assert case.is_recovered is True
    assert case.recovered_amount == case.amount



    # 8. Assert complete immutable audit chain
    audits = await case_repo.get_audit_trail(case.id)
    triggers = [a.trigger for a in audits]

    assert "WEBHOOK_PAYMENT_FAILED" in triggers
    assert "AI_PLAN_SUCCESS" in triggers
    assert "POLICY_EVALUATED" in triggers
    assert "POLICY_APPROVED" in triggers
    assert "RECOVERY_ACTION_CREATED" in triggers
    assert "RAZORPAY_PAYMENT_LINK_CREATED" in triggers
    assert "PAYMENT_LINK_ISSUED" in triggers
    assert "WEBHOOK_PAYMENT_LINK_PAID" in triggers
    assert "RECOVERY_COMPLETED" in triggers
