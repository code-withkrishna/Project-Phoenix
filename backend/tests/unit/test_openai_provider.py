"""Unit tests for the production OpenAICompatibleProvider."""

import httpx
import pytest
import respx

from app.schemas.ai import (
    CustomerHistoryContext,
    DiagnosticContext,
    MerchantConstraintsContext,
    RecommendedAction,
    RecoveryPlan,
    RootCauseCategory,
)
from app.services.ai.openai_provider import OpenAICompatibleProvider


@pytest.fixture
def sample_context() -> DiagnosticContext:
    """Return standard test DiagnosticContext."""
    return DiagnosticContext(
        amount_paise=499900,
        currency="INR",
        payment_id="pay_prod_test_123",
        order_id="order_prod_test_456",
        payment_method="upi",
        error_code="BAD_REQUEST_ERROR",
        error_description="User cancelled authentication",
        error_source="customer",
        error_step="payment_authentication",
        error_reason="payment_cancelled",
        payment_status="failed",
        payment_details={},
        customer_history=CustomerHistoryContext(),
        merchant_constraints=MerchantConstraintsContext(),
    )


@pytest.mark.asyncio
@respx.mock
async def test_openai_provider_success(sample_context: DiagnosticContext):
    """Verify OpenAICompatibleProvider posts to chat completions and extracts plan and usage."""
    expected_plan = {
        "root_cause_category": "USER_FRICTION",
        "confidence_score": 0.95,
        "diagnostic_summary": "Customer cancelled UPI prompt",
        "recommended_action": "DISPATCH_PAYMENT_LINK",
        "urgency": "HIGH",
        "link_expiry_minutes": 30,
        "customer_facing_message": "Complete your order with 1-click checkout.",
    }

    mock_response = {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": RecoveryPlan.model_validate(expected_plan).model_dump_json(),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 60,
            "total_tokens": 210,
        },
    }

    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=mock_response)
    )

    provider = OpenAICompatibleProvider(
        api_key="test_sk_key_12345",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        timeout_seconds=5.0,
    )

    raw_text, metadata = await provider.generate_plan(sample_context)

    assert route.called is True
    # Ensure Authorization header had Bearer key
    last_request = route.calls.last.request
    assert last_request.headers["authorization"] == "Bearer test_sk_key_12345"

    plan = RecoveryPlan.model_validate_json(raw_text)
    assert plan.root_cause_category == RootCauseCategory.USER_FRICTION
    assert plan.recommended_action == RecommendedAction.DISPATCH_PAYMENT_LINK
    assert metadata.prompt_tokens == 150
    assert metadata.completion_tokens == 60
    assert metadata.model_name == "gpt-4o-mini"


@pytest.mark.asyncio
@respx.mock
async def test_openai_provider_timeout_handling(sample_context: DiagnosticContext):
    """Verify timeout produces TimeoutError."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("Read timed out")
    )

    provider = OpenAICompatibleProvider(
        api_key="test_sk_key_12345",
        timeout_seconds=0.1,
    )

    with pytest.raises(TimeoutError) as exc:
        await provider.generate_plan(sample_context)
    assert "timed out" in str(exc.value).lower()


@pytest.mark.asyncio
@respx.mock
async def test_openai_provider_http_error_handling(sample_context: DiagnosticContext):
    """Verify HTTP 4xx/5xx produces handled RuntimeError without credential leakage."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "Rate limit exceeded"})
    )

    provider = OpenAICompatibleProvider(
        api_key="test_sk_key_12345",
    )

    with pytest.raises(RuntimeError) as exc:
        await provider.generate_plan(sample_context)
    assert "429" in str(exc.value)
    # Ensure raw secret is not in exception message
    assert "test_sk_key_12345" not in str(exc.value)
