"""Abstract AI Provider base class."""

from abc import ABC, abstractmethod

from app.schemas.ai import AIDiagnosisMetadata, DiagnosticContext


class AIProvider(ABC):
    """Abstract interface for LLM diagnostic providers."""

    @abstractmethod
    async def generate_plan(
        self,
        context: DiagnosticContext,
        correction_prompt: str | None = None,
    ) -> tuple[str, AIDiagnosisMetadata]:
        """Synthesize a structured recovery plan JSON string and execution metadata.

        Args:
            context: Sanitized diagnostic telemetry and customer history.
            correction_prompt: Optional error details from a failed previous validation attempt.

        Returns:
            A tuple of (raw_json_response_string, AIDiagnosisMetadata).
        """
        pass
