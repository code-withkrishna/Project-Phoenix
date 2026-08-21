"""AI Diagnosis repository."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_diagnosis import AIDiagnosis


class AIDiagnosisRepository:
    """Data access for AI diagnoses."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        case_id: uuid.UUID,
        model_name: str,
        root_cause_category: str,
        confidence_score: float,
        diagnostic_summary: str,
        recommended_action: str,
        suggested_expiry_minutes: int | None = None,
        suggested_customer_note: str | None = None,
        raw_response: dict[str, Any] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int = 0,
    ) -> AIDiagnosis:
        """Create and persist an AI diagnosis record."""
        diagnosis = AIDiagnosis(
            id=uuid.uuid4(),
            case_id=case_id,
            model_name=model_name,
            root_cause_category=root_cause_category,
            confidence_score=confidence_score,
            diagnostic_summary=diagnostic_summary,
            recommended_action=recommended_action,
            suggested_expiry_minutes=suggested_expiry_minutes,
            suggested_customer_note=suggested_customer_note,
            raw_response=raw_response or {},
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        self._session.add(diagnosis)
        await self._session.commit()
        await self._session.refresh(diagnosis)
        return diagnosis

    async def get_by_case_id(self, case_id: uuid.UUID) -> list[AIDiagnosis]:
        """Fetch all diagnoses for a case ordered by creation time."""
        result = await self._session.execute(
            select(AIDiagnosis)
            .where(AIDiagnosis.case_id == case_id)
            .order_by(AIDiagnosis.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_by_case_id(self, case_id: uuid.UUID) -> AIDiagnosis | None:
        """Fetch the most recent diagnosis for a case."""
        result = await self._session.execute(
            select(AIDiagnosis)
            .where(AIDiagnosis.case_id == case_id)
            .order_by(AIDiagnosis.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
