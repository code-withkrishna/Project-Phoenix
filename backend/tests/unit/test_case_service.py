"""Unit tests for recovery case service."""

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.webhook import NormalizedPaymentFailedEvent
from app.services.razorpay.client import RazorpayClient
from app.services.razorpay.reconciliation import PaymentReconciliationService
from app.services.recovery.case_service import RecoveryCaseService
from tests.fixtures.razorpay import payment_failed_payload, razorpay_payment_api_response


def _normalized_event(payment_id: str = "pay_TEST_FIXTURE_001") -> NormalizedPaymentFailedEvent:
    payload = payment_failed_payload(payment_id=payment_id)
    payload.pop("_test_fixture_event_id", None)
    payment = payload["payload"]["payment"]["entity"]
    return NormalizedPaymentFailedEvent(
        event_id="event_TEST_FIXTURE_001",
        payment_id=payment_id,
        order_id=payment["order_id"],
        amount=payment["amount"],
        currency=payment["currency"],
        customer_email=payment["email"],
        customer_phone=payment["contact"],
        failure_code=payment["error_code"],
        failure_reason=payment["error_reason"],
        failure_telemetry={"status": "failed"},
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        razorpay_key_id="rzp_test_fixture",
        razorpay_key_secret="secret_fixture",
    )


@pytest.fixture
def razorpay_client(settings: Settings) -> RazorpayClient:
    return RazorpayClient(settings, client=httpx.AsyncClient(base_url="https://api.razorpay.com"))


@pytest.fixture
def case_service(db_session: AsyncSession, razorpay_client: RazorpayClient) -> RecoveryCaseService:
    reconciliation = PaymentReconciliationService(razorpay_client)
    return RecoveryCaseService(db_session, reconciliation)


@pytest.mark.asyncio
@respx.mock
async def test_idempotent_case_creation(case_service: RecoveryCaseService, db_session: AsyncSession):
    """Duplicate payment_id does not create a second recovery case."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="failed"))
    )

    event = _normalized_event(payment_id)
    await case_service.handle_payment_failed(event)
    await case_service.handle_payment_failed(event)

    repo = RecoveryCaseRepository(db_session)
    case = await repo.get_by_payment_id(payment_id)
    assert case is not None
    assert case.status == "DETECTED"

    cases, total = await repo.list_cases(payment_id=payment_id)
    assert total == 1
    assert len(cases) == 1


@pytest.mark.asyncio
@respx.mock
async def test_case_created_with_detected_state(case_service: RecoveryCaseService, db_session: AsyncSession):
    """Genuinely failed payment creates case in DETECTED state with audit."""
    payment_id = "pay_TEST_FIXTURE_002"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(
            200,
            json=razorpay_payment_api_response(payment_id=payment_id, status="failed"),
        )
    )

    await case_service.handle_payment_failed(_normalized_event(payment_id))

    repo = RecoveryCaseRepository(db_session)
    case = await repo.get_by_payment_id(payment_id)
    assert case is not None
    assert case.status == "DETECTED"
    assert case.payment_id == payment_id
    assert case.amount == 499900

    audit_trail = await repo.get_audit_trail(case.id)
    assert len(audit_trail) == 1
    assert audit_trail[0].to_state == "DETECTED"
    assert audit_trail[0].trigger == "WEBHOOK_PAYMENT_FAILED"


@pytest.mark.asyncio
@respx.mock
async def test_captured_payment_skips_case_creation(
    case_service: RecoveryCaseService,
    db_session: AsyncSession,
):
    """Already captured payment does not create an active recovery case."""
    payment_id = "pay_TEST_FIXTURE_003"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(
            200,
            json=razorpay_payment_api_response(payment_id=payment_id, status="captured"),
        )
    )

    await case_service.handle_payment_failed(_normalized_event(payment_id))

    repo = RecoveryCaseRepository(db_session)
    case = await repo.get_by_payment_id(payment_id)
    assert case is None
