"""Unit tests for the Context Engine and PII sanitization."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recovery_case import RecoveryCase
from app.services.recovery.context_engine import ContextEngine, sanitize_payment_details


@pytest.mark.asyncio
async def test_context_engine_extracts_telemetry_and_history(db_session: AsyncSession):
    """Verify ContextEngine correctly extracts telemetry and aggregates customer history."""
    # 1. Create a prior recovered case for the same customer
    prior_case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id="pay_prior_1",
        order_id="order_prior_1",
        amount=100000,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        status="RECOVERED",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="otp_timeout",
        failure_telemetry={"error_code": "BAD_REQUEST_ERROR"},
        is_recovered=True,
        recovered_amount=100000,
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(prior_case)
    await db_session.commit()

    # 2. Create the current failed case
    current_case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id="pay_current_2",
        order_id="order_current_2",
        amount=250000,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        status="DETECTED",
        failure_code="GATEWAY_ERROR",
        failure_reason="bank_technical_error",
        failure_telemetry={
            "error_code": "GATEWAY_ERROR",
            "error_description": "Bank servers timed out",
            "error_source": "bank",
            "error_step": "payment_authorization",
            "error_reason": "bank_technical_error",
            "method": "upi",
            "vpa": "shopper@okhdfcbank",
            "email": "shopper@example.com",
            "contact": "+919876543210",
        },
    )
    db_session.add(current_case)
    await db_session.commit()

    # 3. Build diagnostic context
    engine = ContextEngine(db_session)
    context = await engine.build_context(current_case)

    # 4. Assert telemetry extraction
    assert context.payment_id == "pay_current_2"
    assert context.amount_paise == 250000
    assert context.currency == "INR"
    assert context.payment_method == "upi"
    assert context.error_code == "GATEWAY_ERROR"
    assert context.error_reason == "bank_technical_error"
    assert context.error_description == "Bank servers timed out"

    # 5. Assert deterministic customer history
    assert context.customer_history.is_repeat_customer is True
    assert context.customer_history.lifetime_recoveries == 1
    assert context.customer_history.prior_failures_today == 1

    # 6. Assert merchant constraints
    assert context.merchant_constraints.min_link_expiry_minutes == 15
    assert context.merchant_constraints.max_link_expiry_minutes == 1440
    assert "DISPATCH_PAYMENT_LINK" in context.merchant_constraints.allowed_actions


@pytest.mark.asyncio
async def test_context_engine_pii_sanitization(db_session: AsyncSession):
    """Verify raw PII is strictly stripped from DiagnosticContext."""
    raw_telemetry = {
        "email": "customer@secret.com",
        "contact": "+919999988888",
        "customer_id": "cust_123456",
        "customer_name": "John Doe",
        "notes": {"secret_token": "tok_xyz"},
        "vpa": "johndoe@okaxis",
        "bank": "HDFC",
    }

    case = RecoveryCase(
        id=uuid.uuid4(),
        payment_id="pay_pii_test",
        amount=50000,
        currency="INR",
        customer_email="customer@secret.com",
        customer_phone="+919999988888",
        status="DETECTED",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry=raw_telemetry,
    )
    db_session.add(case)
    await db_session.commit()

    engine = ContextEngine(db_session)
    context = await engine.build_context(case)

    context_json = context.model_dump_json()

    # Raw PII must NOT appear anywhere in the serialized context sent to LLM
    assert "customer@secret.com" not in context_json
    assert "+919999988888" not in context_json
    assert "John Doe" not in context_json
    assert "tok_xyz" not in context_json
    assert "cust_123456" not in context_json

    # Masked handle is allowed
    assert context.payment_details.get("vpa_handle") == "***@okaxis"
    assert context.payment_details.get("bank") == "HDFC"


def test_sanitize_payment_details_helper():
    """Unit test for payment details sanitizer."""
    telemetry = {
        "bank": "ICICI",
        "wallet": "paytm",
        "card": {
            "network": "Visa",
            "type": "credit",
            "international": False,
            "number": "4111111111111111",  # sensitive
        },
        "vpa": "alice@oksbi",
        "token": "tok_unauthorized",
    }
    safe = sanitize_payment_details(telemetry)
    assert safe["bank"] == "ICICI"
    assert safe["wallet"] == "paytm"
    assert safe["card"] == {"network": "Visa", "type": "credit", "international": False}
    assert safe["vpa_handle"] == "***@oksbi"
    assert "token" not in safe
    assert "number" not in safe.get("card", {})
