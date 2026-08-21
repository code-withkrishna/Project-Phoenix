"""Razorpay webhook test fixtures — TEST FIXTURE payloads only."""

import json

from app.core.security import compute_hmac_sha256_hex

TEST_WEBHOOK_SECRET = "test_webhook_secret_phoenix_wave1"


def payment_failed_payload(
    *,
    payment_id: str = "pay_TEST_FIXTURE_001",
    order_id: str = "order_TEST_FIXTURE_001",
    event_id: str = "event_TEST_FIXTURE_001",
) -> dict:
    """Return a realistic Razorpay payment.failed webhook payload (TEST FIXTURE)."""
    return {
        "entity": "event",
        "account_id": "acc_TEST_FIXTURE",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": 499900,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": order_id,
                    "invoice_id": None,
                    "international": False,
                    "method": "upi",
                    "amount_refunded": 0,
                    "refund_status": None,
                    "captured": False,
                    "description": "TEST FIXTURE purchase",
                    "card_id": None,
                    "bank": None,
                    "wallet": None,
                    "vpa": "customer@oksbi",
                    "email": "testfixture@example.com",
                    "contact": "+919876543210",
                    "customer_id": "cust_TEST_FIXTURE",
                    "notes": {"cart_id": "cart_TEST_FIXTURE"},
                    "fee": None,
                    "tax": None,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment was cancelled by the user on the bank/UPI app",
                    "error_source": "customer",
                    "error_step": "payment_authentication",
                    "error_reason": "payment_cancelled",
                    "created_at": 1755765000,
                }
            }
        },
        "created_at": 1755765001,
        "_test_fixture_event_id": event_id,
    }


def payment_failed_raw_body(
    *,
    payment_id: str = "pay_TEST_FIXTURE_001",
    order_id: str = "order_TEST_FIXTURE_001",
) -> bytes:
    """Serialize TEST FIXTURE payment.failed payload to raw bytes."""
    payload = payment_failed_payload(payment_id=payment_id, order_id=order_id)
    payload.pop("_test_fixture_event_id", None)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def compute_signature(raw_body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    """Compute valid X-Razorpay-Signature for test payloads."""
    return compute_hmac_sha256_hex(raw_body, secret)


def webhook_headers(
    raw_body: bytes,
    *,
    event_id: str = "event_TEST_FIXTURE_001",
    secret: str = TEST_WEBHOOK_SECRET,
) -> dict[str, str]:
    """Build webhook request headers with valid signature."""
    return {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": compute_signature(raw_body, secret),
        "X-Razorpay-Event-Id": event_id,
    }


def razorpay_payment_api_response(
    *,
    payment_id: str = "pay_TEST_FIXTURE_001",
    status: str = "failed",
    order_id: str = "order_TEST_FIXTURE_001",
) -> dict:
    """Return a mock Razorpay GET /v1/payments/{id} response."""
    return {
        "id": payment_id,
        "entity": "payment",
        "amount": 499900,
        "currency": "INR",
        "status": status,
        "order_id": order_id,
        "method": "upi",
        "captured": status == "captured",
        "email": "testfixture@example.com",
        "contact": "+919876543210",
    }


def order_paid_payload(*, event_id: str = "event_TEST_FIXTURE_ORDER_PAID") -> dict:
    """Return an unsupported order.paid webhook payload (TEST FIXTURE)."""
    return {
        "entity": "event",
        "account_id": "acc_TEST_FIXTURE",
        "event": "order.paid",
        "contains": ["order"],
        "payload": {
            "order": {
                "entity": {
                    "id": "order_TEST_FIXTURE_001",
                    "entity": "order",
                    "amount": 499900,
                    "currency": "INR",
                    "status": "paid",
                }
            }
        },
        "created_at": 1755765002,
        "_test_fixture_event_id": event_id,
    }
