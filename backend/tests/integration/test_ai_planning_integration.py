"""Integration tests for the complete AI recovery planning lifecycle."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_diagnosis import AIDiagnosis
from app.models.audit_log import AuditLog
from app.models.recovery_case import RecoveryCase
from app.repositories.ai_diagnoses import AIDiagnosisRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.services.ai.mock import MockAIProvider
from app.services.recovery.context_engine import ContextEngine
from app.services.recovery.planner import AIRecoveryPlanner


@pytest.mark.asyncio
async def test_end_to_end_ai_planning_vertical_slice(db_session: AsyncSession):
    """Test full flow: RecoveryCase -> ContextEngine -> MockAIProvider -> AIDiagnosis -> PLAN_GENERATED state."""
    # 1. Create a recovery case
    case_repo = RecoveryCaseRepository(db_session)
    case, created = await case_repo.create_if_absent(
        payment_id="pay_integration_001",
        order_id="order_integration_001",
        amount=349900,
        currency="INR",
        customer_email="shopper_integration@example.com",
        customer_phone="+919123456780",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="otp_timeout",
        failure_telemetry={
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "OTP expired before authentication",
            "error_source": "customer",
            "error_step": "payment_authentication",
            "error_reason": "otp_timeout",
            "method": "card",
            "card": {"network": "MasterCard", "type": "debit"},
        },
        status="DETECTED",
    )
    assert created is True
    assert case.status == "DETECTED"

    # 2. Run AI Recovery Planner
    provider = MockAIProvider(model_name="mock-ai-integration")
    planner = AIRecoveryPlanner(db_session, ai_provider=provider)
    result = await planner.plan_recovery(case.id)

    # 3. Assert success and state transitions
    assert result.success is True
    assert result.plan is not None
    assert result.diagnosis is not None
    assert result.case.status == "PLAN_GENERATED"

    # 4. Verify AIDiagnosis repository lookup
    diag_repo = AIDiagnosisRepository(db_session)
    saved_diagnoses = await diag_repo.get_by_case_id(case.id)
    assert len(saved_diagnoses) == 1
    assert saved_diagnoses[0].model_name == "mock-ai-integration"
    assert saved_diagnoses[0].root_cause_category == "USER_FRICTION"
    assert saved_diagnoses[0].confidence_score >= 0.90

    # 5. Verify Audit Trail
    audit_trail = await case_repo.get_audit_trail(case.id)
    states = [a.to_state for a in audit_trail]
    assert "DIAGNOSING" in states
    assert "PLAN_GENERATED" in states


@pytest.mark.asyncio
async def test_ai_diagnoses_foreign_key_cascade(db_session: AsyncSession):
    """Verify deleting a RecoveryCase cascades and removes associated AIDiagnosis records."""
    # 1. Create case and generate diagnosis
    case_repo = RecoveryCaseRepository(db_session)
    case, _ = await case_repo.create_if_absent(
        payment_id="pay_cascade_test",
        order_id="order_cascade_test",
        amount=100000,
        currency="INR",
        customer_email="cascade@example.com",
        customer_phone="+919000000000",
        failure_code="GATEWAY_ERROR",
        failure_reason="gateway_timeout",
        failure_telemetry={"error_code": "GATEWAY_ERROR"},
    )

    planner = AIRecoveryPlanner(db_session, ai_provider=MockAIProvider())
    result = await planner.plan_recovery(case)
    assert result.success is True

    # 2. Verify diagnosis exists
    diag_repo = AIDiagnosisRepository(db_session)
    diags = await diag_repo.get_by_case_id(case.id)
    assert len(diags) == 1

    # 3. Delete case
    await db_session.delete(result.case)
    await db_session.commit()

    # 4. Verify diagnoses cascaded
    diags_after = await diag_repo.get_by_case_id(case.id)
    assert len(diags_after) == 0
