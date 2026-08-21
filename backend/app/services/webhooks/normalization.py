"""Webhook payload normalization."""

from typing import Any

from app.schemas.webhook import NormalizedPaymentFailedEvent, NormalizedPaymentLinkEvent



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


def normalize_payment_link_event(
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> NormalizedPaymentLinkEvent:
    """Normalize a Razorpay payment_link.* webhook into Phoenix format."""
    plink = payload.get("payload", {}).get("payment_link", {}).get("entity")
    if not plink:
        raise WebhookNormalizationError(f"Missing payment_link entity in {event_type} payload")

    plink_id = plink.get("id")
    if not plink_id:
        raise WebhookNormalizationError(f"Missing payment_link id in {event_type} payload")

    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity") or {}
    customer = plink.get("customer") or {}

    return NormalizedPaymentLinkEvent(
        event_id=event_id,
        event_type=event_type,
        payment_link_id=str(plink_id),
        reference_id=plink.get("reference_id"),
        payment_link_status=str(plink.get("status", "")),
        amount=int(plink.get("amount", 0)),
        amount_paid=int(plink.get("amount_paid", 0)),
        currency=str(plink.get("currency", "INR")),
        payment_id=payment_entity.get("id"),
        payment_status=payment_entity.get("status"),
        payment_amount=int(payment_entity.get("amount", 0)) if payment_entity.get("amount") is not None else None,
        customer_email=customer.get("email") or payment_entity.get("email"),
        customer_phone=customer.get("contact") or payment_entity.get("contact"),
        raw_payload=payload,
    )

