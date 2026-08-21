"""Security utilities for webhook verification and safe comparisons."""

import hmac
import hashlib


def compute_hmac_sha256_hex(raw_body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 hex digest for a raw payload."""
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_hmac_sha256_signature(
    raw_body: bytes,
    signature: str | None,
    secret: str | None,
) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison."""
    if not signature or not secret:
        return False

    expected = compute_hmac_sha256_hex(raw_body, secret)
    return hmac.compare_digest(expected, signature)
