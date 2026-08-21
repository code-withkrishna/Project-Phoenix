"""Context Engine for assembling sanitized diagnostic payloads for the AI."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import (
    CustomerHistoryContext,
    DiagnosticContext,
    MerchantConstraintsContext,
    RecommendedAction,
)

logger = logging.getLogger(__name__)

# Keys strictly stripped during PII sanitization
FORBIDDEN_PII_KEYS = {
    "email",
    "contact",
    "phone",
    "customer_id",
    "customer_name",
    "name",
    "card_id",
    "token",
    "secret",
    "key",
    "password",
    "notes",
    "auth",
    "signature",
    "pan",
    "account_number",
    "cvv",
}


def sanitize_payment_details(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Sanitize payment metadata, removing all PII and sensitive credentials."""
    safe_details: dict[str, Any] = {}

    # Extract only non-identifying technical metadata
    if "bank" in telemetry and telemetry["bank"]:
        safe_details["bank"] = str(telemetry["bank"])
    if "wallet" in telemetry and telemetry["wallet"]:
        safe_details["wallet"] = str(telemetry["wallet"])
    if "card" in telemetry and isinstance(telemetry["card"], dict):
        card_info = telemetry["card"]
        safe_card: dict[str, Any] = {}
        if "network" in card_info:
            safe_card["network"] = str(card_info["network"])
        if "type" in card_info:
            safe_card["type"] = str(card_info["type"])
        if "international" in card_info:
            safe_card["international"] = bool(card_info["international"])
        if safe_card:
            safe_details["card"] = safe_card
    if "vpa" in telemetry and telemetry["vpa"]:
        # Mask UPI handle provider domain only (e.g., ***@oksbi) to preserve technical bank route without revealing identity
        vpa_str = str(telemetry["vpa"])
        if "@" in vpa_str:
            _, domain = vpa_str.split("@", 1)
            safe_details["vpa_handle"] = f"***@{domain}"
        else:
            safe_details["vpa_handle"] = "masked_vpa"

    return safe_details


class ContextEngine:
    """Extracts failure telemetry, queries customer history, and builds sanitized DiagnosticContext."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = RecoveryCaseRepository(session)

    async def build_context(self, case: RecoveryCase) -> DiagnosticContext:
        """Construct a strict DiagnosticContext with safe derived features and zero raw PII."""
        telemetry = case.failure_telemetry or {}

        # 1. Deterministic customer history aggregation (safe derived features)
        customer_history: CustomerHistoryContext = await self._repo.get_customer_history(
            customer_email=case.customer_email,
            customer_phone=case.customer_phone,
            exclude_case_id=case.id,
        )

        # 2. Extract failure telemetry
        error_code = case.failure_code or telemetry.get("error_code")
        error_reason = case.failure_reason or telemetry.get("error_reason")
        error_description = telemetry.get("error_description")
        error_source = telemetry.get("error_source")
        error_step = telemetry.get("error_step")
        payment_method = telemetry.get("method") or telemetry.get("payment_method")

        # 3. Sanitize payment details
        payment_details = sanitize_payment_details(telemetry)

        # 4. Merchant constraints
        merchant_constraints = MerchantConstraintsContext(
            allowed_actions=[
                RecommendedAction.DISPATCH_PAYMENT_LINK.value,
                RecommendedAction.DO_NOT_RECOVER.value,
            ],
            min_link_expiry_minutes=15,
            max_link_expiry_minutes=1440,
        )

        diagnostic_context = DiagnosticContext(
            amount_paise=case.amount,
            currency=case.currency,
            payment_id=case.payment_id,
            order_id=case.order_id,
            payment_method=payment_method,
            error_code=error_code,
            error_description=error_description,
            error_source=error_source,
            error_step=error_step,
            error_reason=error_reason,
            payment_status=case.status.lower() if case.status else "failed",
            payment_details=payment_details,
            customer_history=customer_history,
            merchant_constraints=merchant_constraints,
        )

        logger.debug(
            "DiagnosticContext built successfully: payment_id=%s repeat_customer=%s",
            case.payment_id,
            customer_history.is_repeat_customer,
        )
        return diagnostic_context
