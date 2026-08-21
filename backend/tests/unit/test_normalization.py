"""Unit tests for webhook normalization."""

import pytest

from app.services.webhooks.normalization import (
    WebhookNormalizationError,
    normalize_payment_failed,
)
from tests.fixtures.razorpay import payment_failed_payload


def test_normalize_payment_failed_success():
    """payment.failed payload normalizes to Phoenix event."""
    payload = payment_failed_payload()
    payload.pop("_test_fixture_event_id", None)
    event = normalize_payment_failed(event_id="event_TEST_001", payload=payload)

    assert event.event_id == "event_TEST_001"
    assert event.payment_id == "pay_TEST_FIXTURE_001"
    assert event.order_id == "order_TEST_FIXTURE_001"
    assert event.amount == 499900
    assert event.currency == "INR"
    assert event.failure_code == "BAD_REQUEST_ERROR"
    assert event.failure_reason == "payment_cancelled"
    assert event.failure_telemetry["payment_method"] == "upi"


def test_normalize_missing_payment_entity():
    """Missing payment entity raises normalization error."""
    with pytest.raises(WebhookNormalizationError, match="Missing payment entity"):
        normalize_payment_failed(event_id="evt_1", payload={"event": "payment.failed"})


def test_normalize_missing_payment_id():
    """Missing payment id raises normalization error."""
    payload = payment_failed_payload()
    payload.pop("_test_fixture_event_id", None)
    payload["payload"]["payment"]["entity"].pop("id")
    with pytest.raises(WebhookNormalizationError, match="Missing payment id"):
        normalize_payment_failed(event_id="evt_1", payload=payload)
