"""Deterministic Mock AI Provider for testing and offline development."""

import json
import time

from app.schemas.ai import (
    AIDiagnosisMetadata,
    DiagnosticContext,
    RecommendedAction,
    RecoveryPlan,
    RootCauseCategory,
    UrgencyLevel,
)
from app.services.ai.base import AIProvider


class MockAIProvider(AIProvider):
    """Deterministic Mock AI Provider returning structured recovery plans."""

    def __init__(
        self,
        *,
        model_name: str = "mock-ai-v1",
        force_invalid_first: bool = False,
        always_invalid: bool = False,
        simulate_timeout: bool = False,
        simulate_error: bool = False,
    ) -> None:
        self.model_name = model_name
        self.force_invalid_first = force_invalid_first
        self.always_invalid = always_invalid
        self.simulate_timeout = simulate_timeout
        self.simulate_error = simulate_error
        self.call_count = 0

    async def generate_plan(
        self,
        context: DiagnosticContext,
        correction_prompt: str | None = None,
    ) -> tuple[str, AIDiagnosisMetadata]:
        """Synthesize deterministic recovery plan."""
        start_time = time.perf_counter()
        self.call_count += 1

        if self.simulate_timeout:
            raise TimeoutError("Mock AI Provider request timed out after 15.0s")

        if self.simulate_error:
            raise RuntimeError("Mock AI Provider internal connection failure")

        if self.always_invalid or (self.force_invalid_first and correction_prompt is None):
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            raw_text = '{"malformed_plan": true, "error": "missing required fields"}'
            metadata = AIDiagnosisMetadata(
                model_name=self.model_name,
                prompt_tokens=50,
                completion_tokens=10,
                latency_ms=latency_ms,
                raw_response={"raw": raw_text},
            )
            return raw_text, metadata

        # Deterministic taxonomy mapping based on telemetry
        error_code = (context.error_code or "").upper()
        error_reason = (context.error_reason or "").lower()

        if any(term in error_reason for term in ["fraud", "stolen", "blacklist", "suspicious"]):
            plan = RecoveryPlan(
                root_cause_category=RootCauseCategory.TERMINAL_OR_SUSPICIOUS,
                confidence_score=0.99,
                diagnostic_summary="Transaction flagged for security risk. Do not attempt recovery.",
                recommended_action=RecommendedAction.DO_NOT_RECOVER,
                urgency=UrgencyLevel.HIGH,
                link_expiry_minutes=15,
                customer_facing_message="Payment could not be completed. Please contact support.",
            )
        elif "insufficient_funds" in error_reason or "low_balance" in error_reason:
            plan = RecoveryPlan(
                root_cause_category=RootCauseCategory.INSUFFICIENT_FUNDS,
                confidence_score=0.88,
                diagnostic_summary="Account balance insufficient. Extended expiry provided for fund transfer.",
                recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
                urgency=UrgencyLevel.LOW,
                link_expiry_minutes=1440,
                customer_facing_message="Your payment could not be processed. Complete it with this link within 24 hours.",
            )
        elif any(term in error_reason for term in ["card_expired", "invalid_card", "invalid_vpa"]):
            plan = RecoveryPlan(
                root_cause_category=RootCauseCategory.INSTRUMENT_INVALID,
                confidence_score=0.92,
                diagnostic_summary="Payment instrument expired or invalid. Enable alternative payment channels.",
                recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
                urgency=UrgencyLevel.MEDIUM,
                link_expiry_minutes=120,
                customer_facing_message="Payment instrument error. Tap here to complete using another payment method.",
            )
        elif any(term in error_reason for term in ["otp_timeout", "payment_cancelled", "incorrect_pin", "user"]):
            plan = RecoveryPlan(
                root_cause_category=RootCauseCategory.USER_FRICTION,
                confidence_score=0.95,
                diagnostic_summary="Customer encountered authentication friction or cancelled prompt.",
                recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
                urgency=UrgencyLevel.HIGH,
                link_expiry_minutes=30,
                customer_facing_message="Complete your order instantly with 1-click checkout.",
            )
        elif "GATEWAY_ERROR" in error_code or any(term in error_reason for term in ["bank", "gateway_timeout", "downtime", "issuer_down"]):
            plan = RecoveryPlan(
                root_cause_category=RootCauseCategory.TECHNICAL_GATEWAY_ERROR,
                confidence_score=0.90,
                diagnostic_summary="Temporary bank gateway error. Retry recommended via alternative payment routes.",
                recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
                urgency=UrgencyLevel.MEDIUM,
                link_expiry_minutes=60,
                customer_facing_message="Bank gateway experienced a technical delay. Tap here to retry your payment.",
            )
        else:
            # Default to USER_FRICTION
            plan = RecoveryPlan(
                root_cause_category=RootCauseCategory.USER_FRICTION,
                confidence_score=0.95,
                diagnostic_summary="Customer encountered authentication friction or cancelled prompt.",
                recommended_action=RecommendedAction.DISPATCH_PAYMENT_LINK,
                urgency=UrgencyLevel.HIGH,
                link_expiry_minutes=30,
                customer_facing_message="Complete your order instantly with 1-click checkout.",
            )


        raw_text = plan.model_dump_json()
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        metadata = AIDiagnosisMetadata(
            model_name=self.model_name,
            prompt_tokens=120,
            completion_tokens=85,
            latency_ms=latency_ms,
            raw_response=json.loads(raw_text),
        )
        return raw_text, metadata
