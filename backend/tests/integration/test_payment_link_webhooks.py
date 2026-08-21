"""Integration tests for Payment Link Webhooks."""

import json
from datetime import datetime, timezone, timedelta
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.audit_log import AuditLog
from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_actions import RecoveryActionRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.services.webhooks.dispatcher import WebhookDispatcher
from tests.fixtures.razorpay import (
    TEST_WEBHOOK_SECRET,
    compute_signature,
    payment_link_cancelled_payload,
    payment_link_expired_payload,
    payment_link_paid_payload,
    payment_link_partially_paid_payload,
    webhook_headers,
)


@pytest.mark.asyncio
async def test_webhook_payment_link_paid_success(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """payment_link.paid webhook transitions RecoveryAction to PAID and RecoveryCase to RECOVERED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_PAID_001",
        order_id="order_WH_PAID_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="AWAITING_PAYMENT",
    )
    assert case is not None

    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_PAID_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        payment_link_url=f"https://rzp.io/i/{plink_id}",
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(minutes=30),
        executed_at=now,
    )
    await db_session.commit()

    payload_dict = payment_link_paid_payload(
        event_id="event_WH_PAID_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        payment_id="pay_CAPTURED_001",
        amount=499900,
        amount_paid=499900,
        payment_status="captured",
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    # 1. Ingest webhook
    response = await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200

    # 2. Dispatch event processing
    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    # 3. Assert case is RECOVERED
    await db_session.refresh(case)
    await db_session.refresh(action)

    assert action.status == "PAID"
    assert case.status == "RECOVERED"
    assert case.is_recovered is True
    assert case.recovered_amount == 499900

    # 4. Assert audit trail contains recovery completion
    audits = await case_repo.get_audit_trail(case.id)
    triggers = [a.trigger for a in audits]
    assert "WEBHOOK_PAYMENT_LINK_PAID" in triggers
    assert "RECOVERY_COMPLETED" in triggers


@pytest.mark.asyncio
async def test_webhook_payment_link_paid_payment_not_captured(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """payment_link.paid with payment status 'authorized' (not captured) does NOT mark RECOVERED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_AUTH_001",
        order_id="order_WH_AUTH_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="AWAITING_PAYMENT",
    )
    assert case is not None
    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_AUTH_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        payment_link_url=f"https://rzp.io/i/{plink_id}",
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(minutes=30),
        executed_at=now,
    )
    await db_session.commit()

    payload_dict = payment_link_paid_payload(
        event_id="event_WH_AUTH_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        payment_id="pay_AUTH_ONLY_001",
        amount=499900,
        amount_paid=499900,
        payment_status="authorized",  # NOT captured!
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    response = await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert response.status_code == 200

    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    await db_session.refresh(case)
    await db_session.refresh(action)

    # Must NOT be marked RECOVERED
    assert action.status == "ISSUED"
    assert case.status == "AWAITING_PAYMENT"
    assert case.is_recovered is False


@pytest.mark.asyncio
async def test_webhook_payment_link_paid_amount_mismatch(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """payment_link.paid with amount mismatch does NOT mark RECOVERED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_MISMATCH_001",
        order_id="order_WH_MISMATCH_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="AWAITING_PAYMENT",
    )
    assert case is not None
    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_MISMATCH_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(minutes=30),
    )
    await db_session.commit()

    payload_dict = payment_link_paid_payload(
        event_id="event_WH_MISMATCH_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        payment_id="pay_MISMATCH_001",
        amount=299900,  # Mismatched amount!
        amount_paid=299900,
        payment_status="captured",
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    await db_session.refresh(case)
    assert case.status == "AWAITING_PAYMENT"
    assert case.is_recovered is False


@pytest.mark.asyncio
async def test_webhook_payment_link_expired(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """payment_link.expired webhook marks action and case as EXPIRED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_EXP_001",
        order_id="order_WH_EXP_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="AWAITING_PAYMENT",
    )
    assert case is not None
    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_EXP_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(minutes=30),
    )
    await db_session.commit()

    payload_dict = payment_link_expired_payload(
        event_id="event_WH_EXP_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        amount=499900,
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    await db_session.refresh(case)
    await db_session.refresh(action)

    assert action.status == "EXPIRED"
    assert case.status == "EXPIRED"


@pytest.mark.asyncio
async def test_webhook_payment_link_cancelled(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """payment_link.cancelled webhook marks action and case as CANCELLED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_CAN_001",
        order_id="order_WH_CAN_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="AWAITING_PAYMENT",
    )
    assert case is not None
    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_CAN_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(minutes=30),
    )
    await db_session.commit()

    payload_dict = payment_link_cancelled_payload(
        event_id="event_WH_CAN_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        amount=499900,
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    await db_session.refresh(case)
    await db_session.refresh(action)

    assert action.status == "CANCELLED"
    assert case.status == "CANCELLED"


@pytest.mark.asyncio
async def test_webhook_payment_link_partially_paid_does_not_recover(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """payment_link.partially_paid audits the event but does NOT mark case RECOVERED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_PARTIAL_001",
        order_id="order_WH_PARTIAL_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="AWAITING_PAYMENT",
    )
    assert case is not None
    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_PARTIAL_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        amount=499900,
        currency="INR",
        status="ISSUED",
        expires_at=now + timedelta(minutes=30),
    )
    await db_session.commit()

    payload_dict = payment_link_partially_paid_payload(
        event_id="event_WH_PARTIAL_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        amount=499900,
        amount_paid=200000,
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    await db_session.refresh(case)
    await db_session.refresh(action)

    assert action.status == "ISSUED"
    assert case.status == "AWAITING_PAYMENT"
    assert case.is_recovered is False


@pytest.mark.asyncio
async def test_protected_terminal_state_late_expired_after_recovered(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    """Out-of-order test: A late arriving payment_link.expired cannot overwrite RECOVERED."""
    case_repo = RecoveryCaseRepository(db_session)
    action_repo = RecoveryActionRepository(db_session)

    case, _ = await case_repo.create_if_absent(
        payment_id="pay_WH_LATE_EXP_001",
        order_id="order_WH_LATE_EXP_01",
        amount=499900,
        currency="INR",
        customer_email="shopper@example.com",
        customer_phone="+919876543210",
        failure_code="BAD_REQUEST_ERROR",
        failure_reason="payment_cancelled",
        failure_telemetry={},
        status="RECOVERED",
    )
    assert case is not None
    case.is_recovered = True
    case.recovered_amount = 499900
    await db_session.flush()

    ref_id = f"PHX_{str(case.id).replace('-', '').upper()[:8]}_01"
    plink_id = "plink_WH_LATE_EXP_001"
    now = datetime.now(timezone.utc)

    action = await action_repo.create(
        case_id=case.id,
        reference_id=ref_id,
        payment_link_id=plink_id,
        amount=499900,
        currency="INR",
        status="PAID",
        expires_at=now + timedelta(minutes=30),
    )
    await db_session.commit()

    payload_dict = payment_link_expired_payload(
        event_id="event_WH_LATE_EXP_001",
        payment_link_id=plink_id,
        reference_id=ref_id,
        amount=499900,
    )
    event_id = payload_dict.pop("_test_fixture_event_id")
    raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    headers = webhook_headers(raw_body, event_id=event_id, secret=settings.razorpay_webhook_secret)

    await client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)

    dispatcher = WebhookDispatcher(db_session, settings)
    try:
        await dispatcher.process_event(event_id)
    finally:
        await dispatcher.close()

    await db_session.refresh(case)
    await db_session.refresh(action)

    # Terminal success state must remain preserved
    assert case.status == "RECOVERED"
    assert case.is_recovered is True
    assert action.status == "PAID"
