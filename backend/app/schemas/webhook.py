"""Webhook request and response schemas."""

from pydantic import BaseModel, Field


class WebhookAckResponse(BaseModel):
    """Acknowledgement returned after webhook ingestion."""

    status: str = "acknowledged"
    event_id: str
    event_type: str
    action_taken: str = Field(
        description="INGESTED for new events, DEDUPLICATED for replays",
    )


class NormalizedPaymentFailedEvent(BaseModel):
    """Phoenix-normalized representation of a payment.failed webhook."""

    event_id: str
    event_type: str = "payment.failed"
    payment_id: str
    order_id: str | None = None
    amount: int
    currency: str = "INR"
    customer_email: str | None = None
    customer_phone: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    failure_telemetry: dict = Field(default_factory=dict)
    raw_payload: dict = Field(default_factory=dict)
