"""Unit tests for HMAC-SHA256 webhook signature verification."""

import pytest

from app.core.security import compute_hmac_sha256_hex, verify_hmac_sha256_signature
from app.services.razorpay.webhook_verifier import verify_razorpay_webhook_signature
from tests.fixtures.razorpay import (
    TEST_WEBHOOK_SECRET,
    compute_signature,
    payment_failed_raw_body,
)


def test_valid_signature():
    """Valid signature verifies successfully."""
    raw_body = payment_failed_raw_body()
    signature = compute_signature(raw_body)
    assert verify_razorpay_webhook_signature(raw_body, signature, TEST_WEBHOOK_SECRET)


def test_invalid_signature():
    """Tampered signature is rejected."""
    raw_body = payment_failed_raw_body()
    assert not verify_razorpay_webhook_signature(
        raw_body,
        "deadbeef" * 8,
        TEST_WEBHOOK_SECRET,
    )


def test_missing_signature():
    """Missing signature header fails closed."""
    raw_body = payment_failed_raw_body()
    assert not verify_razorpay_webhook_signature(raw_body, None, TEST_WEBHOOK_SECRET)


def test_modified_body():
    """Modified body invalidates signature."""
    raw_body = payment_failed_raw_body()
    signature = compute_signature(raw_body)
    tampered = raw_body.replace(b"499900", b"999900")
    assert not verify_razorpay_webhook_signature(tampered, signature, TEST_WEBHOOK_SECRET)


def test_empty_secret():
    """Empty webhook secret fails closed."""
    raw_body = payment_failed_raw_body()
    signature = compute_signature(raw_body)
    assert not verify_razorpay_webhook_signature(raw_body, signature, "")


def test_compute_hmac_is_deterministic():
    """HMAC helper produces stable hex digest."""
    raw_body = b'{"event":"payment.failed"}'
    expected = compute_hmac_sha256_hex(raw_body, TEST_WEBHOOK_SECRET)
    assert expected == compute_signature(raw_body)
    assert len(expected) == 64
