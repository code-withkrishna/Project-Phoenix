"""Razorpay webhook gateway endpoints."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.logging import bind_log_context, new_correlation_id
from app.schemas.webhook import WebhookAckResponse
from app.services.razorpay.webhook_verifier import verify_razorpay_webhook_signature
from app.services.webhooks.dispatcher import process_webhook_event_background
from app.services.webhooks.ingestion import WebhookIngestionError, WebhookIngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/razorpay", response_model=WebhookAckResponse)
async def receive_razorpay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookAckResponse:
    """Verify, persist, and acknowledge Razorpay webhook events."""
    correlation_id = new_correlation_id()
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    event_id = request.headers.get("X-Razorpay-Event-Id")

    if not settings.razorpay_webhook_secret:
        logger.error("Webhook secret not configured")
        raise HTTPException(status_code=401, detail="Webhook verification failed")

    if not verify_razorpay_webhook_signature(
        raw_body,
        signature,
        settings.razorpay_webhook_secret,
    ):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if not event_id:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Event-Id header")

    bind_log_context(correlation_id=correlation_id, webhook_event_id=event_id)

    ingestion_service = WebhookIngestionService(session)
    try:
        payload = ingestion_service.parse_payload(raw_body)
    except WebhookIngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    event_type = str(payload.get("event", "unknown"))

    _event, created = await ingestion_service.ingest(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        signature=signature or "",
    )

    if created:
        background_tasks.add_task(
            process_webhook_event_background,
            event_id,
            settings.database_url,
        )

    action_taken = "INGESTED" if created else "DEDUPLICATED"
    logger.info(
        "Webhook acknowledged: event_id=%s event_type=%s action=%s",
        event_id,
        event_type,
        action_taken,
    )

    return WebhookAckResponse(
        event_id=event_id,
        event_type=event_type,
        action_taken=action_taken,
    )
