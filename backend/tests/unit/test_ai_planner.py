"""Unit tests for AIRecoveryPlanner orchestration, bounded retry, and fail-closed state management."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_diagnosis import AIDiagnosis
from app.models.audit_log import AuditLog
from app.models.recovery_case import RecoveryCase
from app.services.ai.mock import MockAIProvider
from app.services.recovery.planner import AIRecoveryPlanner, sanitize_raw_response


@pytest.fixture
async def sample_case(db_session: AsyncSession) -> RecoveryCase:
    """Create and persist a test RecoveryCase."""
    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id=f"pay_planner_test_{uuid.uuid4().hex[:8]}",
        order_id="order_planner_1",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        status="DETECTED",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment was cancelled by the user",
            "method": "upi",
            "vpa": "shopper@oksbi",
        },
    )
    db_session.add(case)
    await db_session.commit()
    await db_session.refresh(case)
    return case


@pytest.mark.asyncio
async def test_planner_success_flow(db_session: AsyncSession, sample_case: RecoveryCase):
    """Successful AI plan generates AIDiagnosis and transitions case to PLAN_GENERATED."""
    provider = MockAIProvider()
    planner = AIRecoveryPlanner(db_session, ai_provider=provider)

    result = await planner.plan_recovery(sample_case)

    assert result.success is True
    assert result.plan is not None
    assert result.diagnosis is not None
    assert result.case.status == "PLAN_GENERATED"

    # Verify AIDiagnosis in DB
    diagnoses_res = await db_session.execute(
        select(AIDiagnosis).where(AIDiagnosis.case_id == sample_case.id)
    )
    saved_diagnosis = diagnoses_res.scalar_one()
    assert saved_diagnosis.root_cause_category == "USER_FRICTION"
    assert saved_diagnosis.recommended_action == "DISPATCH_PAYMENT_LINK"
    assert saved_diagnosis.confidence_score >= 0.90

    # Verify Audit Logs
    audit_res = await db_session.execute(
        select(AuditLog).where(AuditLog.case_id == sample_case.id).order_by(AuditLog.created_at.asc())
    )
    audit_entries = audit_res.scalars().all()
    triggers = [e.trigger for e in audit_entries]
    to_states = [e.to_state for e in audit_entries]

    assert "AI_DIAGNOSIS_TRIGGERED" in triggers
    assert "AI_PLAN_SUCCESS" in triggers
    assert "DIAGNOSING" in to_states
    assert "PLAN_GENERATED" in to_states


@pytest.mark.asyncio
async def test_planner_bounded_correction_success(
    db_session: AsyncSession, sample_case: RecoveryCase
):
    """Planner performs bounded correction on attempt 1 failure and succeeds on attempt 2."""
    provider = MockAIProvider(force_invalid_first=True)
    planner = AIRecoveryPlanner(db_session, ai_provider=provider)

    result = await planner.plan_recovery(sample_case)

    assert result.success is True
    assert result.case.status == "PLAN_GENERATED"
    assert provider.call_count == 2  # Exactly 2 calls (1 initial + 1 correction)


@pytest.mark.asyncio
async def test_planner_fail_closed_after_failed_correction(
    db_session: AsyncSession, sample_case: RecoveryCase
):
    """Planner fails closed to ESCALATED if attempt 2 still fails schema validation."""
    provider = MockAIProvider(always_invalid=True)
    planner = AIRecoveryPlanner(db_session, ai_provider=provider)

    result = await planner.plan_recovery(sample_case)

    assert result.success is False
    assert result.case.status == "ESCALATED"
    assert result.error is not None
    assert provider.call_count == 2  # Bounded to exactly 1 retry

    # Verify audit log recorded failure
    audit_trail = await planner._case_repo.get_audit_trail(sample_case.id)
    states = [a.to_state for a in audit_trail]
    triggers = [a.trigger for a in audit_trail]
    assert "DIAGNOSING" in states
    assert "ESCALATED" in states
    assert "AI_PLAN_ERROR" in triggers
    assert audit_trail[-1].to_state == "ESCALATED"



@pytest.mark.asyncio
async def test_planner_fail_closed_on_timeout(
    db_session: AsyncSession, sample_case: RecoveryCase
):
    """Planner fails closed to ESCALATED on AI provider timeout."""
    provider = MockAIProvider(simulate_timeout=True)
    planner = AIRecoveryPlanner(db_session, ai_provider=provider)

    result = await planner.plan_recovery(sample_case)

    assert result.success is False
    assert result.case.status == "ESCALATED"
    assert "timed out" in (result.error or "").lower()


def test_sanitize_raw_response_redacts_sensitive_keys():
    """Verify raw response sanitizer redacts sensitive fields before DB persistence."""
    raw = {
        "diagnostic_summary": "User cancelled",
        "customer_email": "leaked@example.com",
        "phone": "+919876543210",
        "nested": {
            "token": "secret_abc",
            "safe_field": 123,
        },
    }
    sanitized = sanitize_raw_response(raw)
    assert sanitized["diagnostic_summary"] == "User cancelled"
    assert sanitized["customer_email"] == "[REDACTED]"
    assert sanitized["phone"] == "[REDACTED]"
    assert sanitized["nested"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["safe_field"] == 123
