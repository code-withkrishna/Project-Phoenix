"""Webhook payload normalization."""

from typing import Any

from app.schemas.webhook import NormalizedPaymentFailedEvent


class WebhookNormalizationError(Exception):
    """Raised when a webhook payload cannot be normalized."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def normalize_payment_failed(
    *,
    event_id: str,
    payload: dict[str, Any],
) -> NormalizedPaymentFailedEvent:
    """Normalize a Razorpay payment.failed webhook into Phoenix format."""
    payment = payload.get("payload", {}).get("payment", {}).get("entity")
    if not payment:
        raise WebhookNormalizationError("Missing payment entity in payment.failed payload")

    payment_id = payment.get("id")
    if not payment_id:
        raise WebhookNormalizationError("Missing payment id in payment.failed payload")

    failure_telemetry = {
        "error_code": payment.get("error_code"),
        "error_description": payment.get("error_description"),
        "error_source": payment.get("error_source"),
        "error_step": payment.get("error_step"),
        "error_reason": payment.get("error_reason"),
        "payment_method": payment.get("method"),
        "vpa": payment.get("vpa"),
        "bank": payment.get("bank"),
        "wallet": payment.get("wallet"),
        "status": payment.get("status"),
    }

    return NormalizedPaymentFailedEvent(
        event_id=event_id,
        payment_id=str(payment_id),
        order_id=payment.get("order_id"),
        amount=int(payment.get("amount", 0)),
        currency=str(payment.get("currency", "INR")),
        customer_email=payment.get("email"),
        customer_phone=payment.get("contact"),
        failure_code=payment.get("error_code"),
        failure_reason=payment.get("error_reason"),
        failure_telemetry=failure_telemetry,
        raw_payload=payload,
    )
