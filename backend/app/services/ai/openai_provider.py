"""Production AI Provider using OpenAI-compatible async HTTP API."""

import json
import logging
import time

import httpx

from app.schemas.ai import AIDiagnosisMetadata, DiagnosticContext
from app.services.ai.base import AIProvider

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Diagnostic Reasoning Core for Project Phoenix, an enterprise payment recovery orchestrator for Razorpay merchants.

Your task is to analyze failed payment telemetry and synthesize a precise, policy-compliant recovery plan.

CORE RULES:
1. Always base your diagnosis strictly on the provided error_telemetry and payment_details.
2. Distinguish accurately between transient user friction (e.g. cancelled MPIN / timeout) vs hard financial/technical failures.
3. If the failure indicates fraud, invalid credentials, or blacklisted instruments, select TERMINAL_OR_SUSPICIOUS and recommended_action = "DO_NOT_RECOVER".
4. Recommend payment link expiry durations that match the root cause (e.g., 15-30m for user friction, 1440m for insufficient funds).
5. Customer-facing messages must be professional, reassuring, and concise (under 160 characters).
6. Output MUST strictly match the following JSON schema:
{
  "root_cause_category": "USER_FRICTION" | "TECHNICAL_GATEWAY_ERROR" | "INSUFFICIENT_FUNDS" | "INSTRUMENT_INVALID" | "TERMINAL_OR_SUSPICIOUS",
  "confidence_score": 0.0 to 1.0,
  "diagnostic_summary": "max 300 characters",
  "recommended_action": "DISPATCH_PAYMENT_LINK" | "DO_NOT_RECOVER",
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "link_expiry_minutes": 15 to 1440,
  "customer_facing_message": "max 160 characters"
}
Output pure JSON with no markdown wrapping.
"""


class OpenAICompatibleProvider(AIProvider):
    """Production AI Provider communicating over async HTTP with OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def generate_plan(
        self,
        context: DiagnosticContext,
        correction_prompt: str | None = None,
    ) -> tuple[str, AIDiagnosisMetadata]:
        """Call LLM endpoint asynchronously with structured output enforcement."""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Analyze this failed payment context and generate a RecoveryPlan:\n{context.model_dump_json(indent=2)}",
            },
        ]

        if correction_prompt:
            messages.append(
                {
                    "role": "user",
                    "content": f"Your previous response failed schema validation with error:\n{correction_prompt}\nPlease correct the JSON output strictly according to the schema.",
                }
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        start_time = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            logger.error("AI Provider request timed out after %.1fs", self.timeout_seconds)
            raise TimeoutError(f"AI Provider request timed out after {self.timeout_seconds}s") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("AI Provider returned HTTP error status %s", exc.response.status_code)
            raise RuntimeError(f"AI Provider HTTP error {exc.response.status_code}") from exc
        except Exception as exc:
            logger.error("AI Provider unexpected connection error: %s", type(exc).__name__)
            raise RuntimeError("AI Provider connection failed") from exc

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("AI Provider returned empty choices array")

        raw_content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")

        parsed_json: dict = {}
        try:
            parsed_json = json.loads(raw_content)
        except Exception:
            parsed_json = {"raw": raw_content}

        metadata = AIDiagnosisMetadata(
            model_name=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            raw_response=parsed_json,
        )

        return raw_content, metadata
