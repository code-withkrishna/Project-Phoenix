"""Unit tests for payment reconciliation service."""

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.razorpay.client import RazorpayAPIError, RazorpayClient
from app.services.razorpay.reconciliation import PaymentReconciliationService
from tests.fixtures.razorpay import razorpay_payment_api_response


@pytest.fixture
def settings() -> Settings:
    return Settings(
        razorpay_key_id="rzp_test_fixture",
        razorpay_key_secret="secret_fixture",
        razorpay_api_base_url="https://api.razorpay.com",
    )


@pytest.fixture
def razorpay_client(settings: Settings) -> RazorpayClient:
    return RazorpayClient(settings, client=httpx.AsyncClient(base_url=settings.razorpay_api_base_url))


@pytest.fixture
def reconciliation_service(razorpay_client: RazorpayClient) -> PaymentReconciliationService:
    return PaymentReconciliationService(razorpay_client)


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_failed_payment(reconciliation_service):
    """Failed payment status is classified as failed."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="failed"))
    )

    result = await reconciliation_service.reconcile_payment(payment_id)

    assert result.is_failed is True
    assert result.is_resolved is False
    assert result.authoritative_status == "failed"


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_captured_payment(reconciliation_service):
    """Captured payment is classified as resolved."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="captured"))
    )

    result = await reconciliation_service.reconcile_payment(payment_id)

    assert result.is_resolved is True
    assert result.is_failed is False
    assert result.authoritative_status == "captured"


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_state_changed_to_captured(reconciliation_service):
    """Webhook said failed but API reports captured — resolved externally."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(200, json=razorpay_payment_api_response(status="authorized"))
    )

    result = await reconciliation_service.reconcile_payment(payment_id)

    assert result.is_resolved is True
    assert result.authoritative_status == "authorized"


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_timeout(settings):
    """Timeout from Razorpay API raises RazorpayAPIError."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    client = RazorpayClient(settings, client=httpx.AsyncClient(base_url=settings.razorpay_api_base_url))
    service = PaymentReconciliationService(client)

    with pytest.raises(RazorpayAPIError) as exc_info:
        await service.reconcile_payment(payment_id)
    assert exc_info.value.status_code == 408


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_4xx_error(settings):
    """4xx from Razorpay API raises RazorpayAPIError."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(404, text="Not Found")
    )

    client = RazorpayClient(settings, client=httpx.AsyncClient(base_url=settings.razorpay_api_base_url))
    service = PaymentReconciliationService(client)

    with pytest.raises(RazorpayAPIError) as exc_info:
        await service.reconcile_payment(payment_id)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_5xx_error(settings):
    """5xx from Razorpay API raises RazorpayAPIError."""
    payment_id = "pay_TEST_FIXTURE_001"
    respx.get(f"https://api.razorpay.com/v1/payments/{payment_id}").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    client = RazorpayClient(settings, client=httpx.AsyncClient(base_url=settings.razorpay_api_base_url))
    service = PaymentReconciliationService(client)

    with pytest.raises(RazorpayAPIError) as exc_info:
        await service.reconcile_payment(payment_id)
    assert exc_info.value.status_code == 500
