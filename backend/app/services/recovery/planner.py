"""AI Recovery Planner service."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_diagnosis import AIDiagnosis
from app.models.recovery_case import RecoveryCase
from app.repositories.ai_diagnoses import AIDiagnosisRepository
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.schemas.ai import AIDiagnosisMetadata, RecoveryPlan
from app.services.ai.base import AIProvider
from app.services.ai.factory import get_ai_provider
from app.services.recovery.context_engine import ContextEngine
from app.services.recovery.economics import EconomicDecisionEngine

logger = logging.getLogger(__name__)


def sanitize_raw_response(raw: dict[str, Any] | str) -> dict[str, Any]:
    """Ensure raw response stored in the database does not contain sensitive PII or credentials."""
    if isinstance(raw, str):
        try:
            raw_dict = json.loads(raw)
        except Exception:
            # Mask potential phone/card patterns in raw text
            masked_text = re.sub(r"\b\d{10,16}\b", "[REDACTED_NUMERIC]", raw)
            return {"raw_text": masked_text}
    else:
        raw_dict = dict(raw)

    # Redact sensitive keys if present
    sensitive_keys = {"email", "phone", "contact", "card", "token", "secret", "password", "key", "vpa"}
    sanitized: dict[str, Any] = {}
    for k, v in raw_dict.items():
        if any(s in k.lower() for s in sensitive_keys):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_raw_response(v)
        else:
            sanitized[k] = v
    return sanitized


@dataclass
class PlannerResult:
    """Outcome of an AI planning execution."""

    success: bool
    case: RecoveryCase
    plan: RecoveryPlan | None = None
    diagnosis: AIDiagnosis | None = None
    error: str | None = None


class AIRecoveryPlanner:
    """Orchestrates diagnostic context assembly, AI plan synthesis, validation, and persistence."""

    def __init__(
        self,
        session: AsyncSession,
        ai_provider: AIProvider | None = None,
        economic_engine: EconomicDecisionEngine | None = None,
    ) -> None:
        self._session = session
        self._case_repo = RecoveryCaseRepository(session)
        self._diagnosis_repo = AIDiagnosisRepository(session)
        self._context_engine = ContextEngine(session)
        self._provider = ai_provider or get_ai_provider()
        self._economics = economic_engine or EconomicDecisionEngine()

    async def plan_recovery(self, case_or_id: RecoveryCase | UUID) -> PlannerResult:
        """Execute AI diagnostic and planning workflow for a recovery case.

        Workflow:
            1. Load case and transition status to DIAGNOSING.
            2. Build sanitized DiagnosticContext.
            3. Call AIProvider for structured plan.
            4. Strictly validate RecoveryPlan (with at most ONE bounded correction).
            5. Run Economic Decision Engine for Expected Net Recovery.
            6. On success: Persist AIDiagnosis, transition to PLAN_GENERATED, and log audit.
            7. On failure: Fail closed, transition to ESCALATED, and log audit.
        """
        if isinstance(case_or_id, UUID):
            case = await self._case_repo.get_by_id(case_or_id)
            if case is None:
                raise ValueError(f"RecoveryCase with id {case_or_id} not found")
        else:
            case = case_or_id

        # 1. Transition state machine to DIAGNOSING using existing repository mechanism
        case = await self._case_repo.update_status(
            case,
            new_status="DIAGNOSING",
            trigger="AI_DIAGNOSIS_TRIGGERED",
            actor="AI_RECOVERY_PLANNER",
            context_metadata={"previous_status": case.status},
        )

        try:
            # 2. Build sanitized DiagnosticContext
            context = await self._context_engine.build_context(case)

            # 3. Call AIProvider (Attempt 1)
            raw_text, metadata = await self._provider.generate_plan(context)

            # 4. Strict Pydantic validation with ONE bounded self-correction attempt
            plan = None
            validation_error_str = None
            try:
                plan = RecoveryPlan.model_validate_json(raw_text)
            except (ValidationError, ValueError) as exc:
                validation_error_str = str(exc)
                logger.warning(
                    "AI plan validation failed on attempt 1 for case %s: %s. Attempting bounded correction.",
                    case.id,
                    validation_error_str,
                )

            # Attempt 2: Bounded self-correction if attempt 1 failed
            if plan is None and validation_error_str is not None:
                try:
                    raw_text, metadata = await self._provider.generate_plan(
                        context,
                        correction_prompt=validation_error_str,
                    )
                    plan = RecoveryPlan.model_validate_json(raw_text)
                    logger.info("AI bounded self-correction succeeded on attempt 2 for case %s", case.id)
                except (ValidationError, ValueError) as exc:
                    logger.error(
                        "AI bounded self-correction failed on attempt 2 for case %s: %s",
                        case.id,
                        exc,
                    )
                    raise ValueError(f"AI output failed schema validation after correction: {exc}") from exc

            if plan is None:
                raise ValueError("Failed to generate a valid RecoveryPlan")

            # 5. Calculate Expected Net Recovery & Economic Breakdown
            econ_eval = self._economics.evaluate(
                context=context,
                root_cause=plan.root_cause_category,
                raw_confidence=plan.confidence_score,
                proposed_action=plan.recommended_action,
            )
            plan.expected_net_recovery_paise = econ_eval.expected_net_recovery_paise
            plan.recovery_probability = econ_eval.recovery_probability
            plan.economic_reasons = econ_eval.reasons

            # 6. Persist AIDiagnosis record with sanitized raw_response and economic evaluation
            sanitized_response = sanitize_raw_response(metadata.raw_response)
            if isinstance(sanitized_response, dict):
                sanitized_response["economic_evaluation"] = econ_eval.to_dict()

            diagnosis = await self._diagnosis_repo.create(
                case_id=case.id,
                model_name=metadata.model_name,
                root_cause_category=plan.root_cause_category.value,
                confidence_score=plan.confidence_score,
                diagnostic_summary=plan.diagnostic_summary,
                recommended_action=plan.recommended_action.value,
                suggested_expiry_minutes=plan.link_expiry_minutes,
                suggested_customer_note=plan.customer_facing_message,
                raw_response=sanitized_response,
                prompt_tokens=metadata.prompt_tokens,
                completion_tokens=metadata.completion_tokens,
                latency_ms=metadata.latency_ms,
            )

            # 7. Transition state machine to PLAN_GENERATED
            case = await self._case_repo.update_status(
                case,
                new_status="PLAN_GENERATED",
                trigger="AI_PLAN_SUCCESS",
                actor="AI_RECOVERY_PLANNER",
                context_metadata={
                    "diagnosis_id": str(diagnosis.id),
                    "model_name": metadata.model_name,
                    "root_cause_category": plan.root_cause_category.value,
                    "confidence_score": plan.confidence_score,
                    "recommended_action": plan.recommended_action.value,
                    "expected_net_recovery_paise": plan.expected_net_recovery_paise,
                    "is_economically_viable": econ_eval.is_economically_viable,
                    "latency_ms": metadata.latency_ms,
                },
            )

            logger.info(
                "AI Recovery Plan generated successfully: case_id=%s root_cause=%s action=%s ENR=₹%.2f",
                case.id,
                plan.root_cause_category.value,
                plan.recommended_action.value,
                (plan.expected_net_recovery_paise or 0) / 100,
            )

            return PlannerResult(
                success=True,
                case=case,
                plan=plan,
                diagnosis=diagnosis,
            )

        except Exception as exc:
            # 7. Fail closed: Transition to ESCALATED on any AI failure
            error_msg = str(exc)
            logger.error("AI Recovery Planner failed closed for case %s: %s", case.id, error_msg)

            case = await self._case_repo.update_status(
                case,
                new_status="ESCALATED",
                trigger="AI_PLAN_ERROR",
                actor="AI_RECOVERY_PLANNER",
                context_metadata={
                    "error": error_msg,
                    "error_type": type(exc).__name__,
                },
            )

            return PlannerResult(
                success=False,
                case=case,
                error=error_msg,
            )
