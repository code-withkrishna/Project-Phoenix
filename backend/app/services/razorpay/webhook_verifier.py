"""Razorpay webhook signature verification."""

from app.core.security import verify_hmac_sha256_signature


def verify_razorpay_webhook_signature(
    raw_body: bytes,
    signature: str | None,
    webhook_secret: str | None,
) -> bool:
    """Verify X-Razorpay-Signature against raw request body."""
    return verify_hmac_sha256_signature(raw_body, signature, webhook_secret)
