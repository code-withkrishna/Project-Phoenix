"""Unit tests for MockAIProvider behavior and simulation modes."""

import pytest

from app.schemas.ai import (
    CustomerHistoryContext,
    DiagnosticContext,
    MerchantConstraintsContext,
    RecommendedAction,
    RecoveryPlan,
    RootCauseCategory,
)
from app.services.ai.mock import MockAIProvider


@pytest.fixture
def sample_context() -> DiagnosticContext:
    """Return a standard DiagnosticContext."""
    return DiagnosticContext(
        amount_paise=199900,
        currency="INR",
        payment_id="pay_sample_123",
        order_id="order_sample_456",
        payment_method="upi",
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment was cancelled by the user",
        error_source="customer",
        error_step="payment_authentication",
        error_reason="payment_cancelled",
        payment_status="failed",
        payment_details={},
        customer_history=CustomerHistoryContext(),
        merchant_constraints=MerchantConstraintsContext(),
    )


@pytest.mark.asyncio
async def test_mock_provider_user_friction(sample_context: DiagnosticContext):
    """Mock provider diagnoses user cancellation as USER_FRICTION."""
    provider = MockAIProvider()
    raw_text, metadata = await provider.generate_plan(sample_context)

    plan = RecoveryPlan.model_validate_json(raw_text)
    assert plan.root_cause_category == RootCauseCategory.USER_FRICTION
    assert plan.recommended_action == RecommendedAction.DISPATCH_PAYMENT_LINK
    assert plan.confidence_score >= 0.90
    assert metadata.model_name == "mock-ai-v1"


@pytest.mark.asyncio
async def test_mock_provider_gateway_error(sample_context: DiagnosticContext):
    """Mock provider diagnoses gateway error as TECHNICAL_GATEWAY_ERROR."""
    sample_context.error_code = "GATEWAY_ERROR"
    sample_context.error_reason = "bank_technical_error"

    provider = MockAIProvider()
    raw_text, _ = await provider.generate_plan(sample_context)

    plan = RecoveryPlan.model_validate_json(raw_text)
    assert plan.root_cause_category == RootCauseCategory.TECHNICAL_GATEWAY_ERROR
    assert plan.link_expiry_minutes == 60


@pytest.mark.asyncio
async def test_mock_provider_insufficient_funds(sample_context: DiagnosticContext):
    """Mock provider diagnoses low balance as INSUFFICIENT_FUNDS with 24h expiry."""
    sample_context.error_reason = "insufficient_funds"

    provider = MockAIProvider()
    raw_text, _ = await provider.generate_plan(sample_context)

    plan = RecoveryPlan.model_validate_json(raw_text)
    assert plan.root_cause_category == RootCauseCategory.INSUFFICIENT_FUNDS
    assert plan.link_expiry_minutes == 1440


@pytest.mark.asyncio
async def test_mock_provider_fraud_suspicious(sample_context: DiagnosticContext):
    """Mock provider diagnoses fraud as TERMINAL_OR_SUSPICIOUS and DO_NOT_RECOVER."""
    sample_context.error_reason = "stolen_card_blacklisted"

    provider = MockAIProvider()
    raw_text, _ = await provider.generate_plan(sample_context)

    plan = RecoveryPlan.model_validate_json(raw_text)
    assert plan.root_cause_category == RootCauseCategory.TERMINAL_OR_SUSPICIOUS
    assert plan.recommended_action == RecommendedAction.DO_NOT_RECOVER


@pytest.mark.asyncio
async def test_mock_provider_timeout_simulation(sample_context: DiagnosticContext):
    """Mock provider raises TimeoutError when simulate_timeout=True."""
    provider = MockAIProvider(simulate_timeout=True)
    with pytest.raises(TimeoutError):
        await provider.generate_plan(sample_context)


@pytest.mark.asyncio
async def test_mock_provider_invalid_first_correction(sample_context: DiagnosticContext):
    """Mock provider returns malformed on first call, valid on correction."""
    provider = MockAIProvider(force_invalid_first=True)

    # First call: malformed
    raw_1, _ = await provider.generate_plan(sample_context)
    with pytest.raises(Exception):
        RecoveryPlan.model_validate_json(raw_1)

    # Second call with correction prompt: valid
    raw_2, _ = await provider.generate_plan(sample_context, correction_prompt="missing fields")
    plan = RecoveryPlan.model_validate_json(raw_2)
    assert plan.root_cause_category == RootCauseCategory.USER_FRICTION
