"""Payment state reconciliation against Razorpay API."""

import logging
from dataclasses import dataclass
from typing import Any

from app.services.razorpay.client import RazorpayAPIError, RazorpayClient

logger = logging.getLogger(__name__)

RESOLVED_STATUSES = frozenset({"captured", "authorized"})
FAILED_STATUSES = frozenset({"failed"})


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of authoritative payment reconciliation."""

    payment_id: str
    authoritative_status: str
    is_resolved: bool
    is_failed: bool
    payment_data: dict[str, Any]


class PaymentReconciliationService:
    """Reconcile webhook-reported failures with Razorpay API truth."""

    def __init__(self, razorpay_client: RazorpayClient) -> None:
        self._client = razorpay_client

    async def reconcile_payment(self, payment_id: str) -> ReconciliationResult:
        """Fetch payment from Razorpay and classify reconciliation outcome."""
        payment_data = await self._client.get_payment(payment_id)
        status = str(payment_data.get("status", "")).lower()

        result = ReconciliationResult(
            payment_id=payment_id,
            authoritative_status=status,
            is_resolved=status in RESOLVED_STATUSES,
            is_failed=status in FAILED_STATUSES,
            payment_data=payment_data,
        )
        logger.info(
            "Payment reconciled: payment_id=%s status=%s resolved=%s failed=%s",
            payment_id,
            status,
            result.is_resolved,
            result.is_failed,
        )
        return result

    @staticmethod
    def classify_without_api(webhook_status: str) -> ReconciliationResult:
        """Fallback classification using webhook-reported status only."""
        status = webhook_status.lower()
        return ReconciliationResult(
            payment_id="",
            authoritative_status=status,
            is_resolved=status in RESOLVED_STATUSES,
            is_failed=status in FAILED_STATUSES,
            payment_data={"status": status},
        )
