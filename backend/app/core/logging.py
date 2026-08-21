"""Structured JSON logging configuration."""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
webhook_event_id_var: ContextVar[str | None] = ContextVar("webhook_event_id", default=None)
payment_id_var: ContextVar[str | None] = ContextVar("payment_id", default=None)
recovery_case_id_var: ContextVar[str | None] = ContextVar("recovery_case_id", default=None)


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    _CONTEXT_FIELDS = (
        ("correlation_id", correlation_id_var),
        ("webhook_event_id", webhook_event_id_var),
        ("payment_id", payment_id_var),
        ("recovery_case_id", recovery_case_id_var),
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field_name, context_var in self._CONTEXT_FIELDS:
            value = context_var.get()
            if value is not None:
                payload[field_name] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with structured JSON output."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)


def new_correlation_id() -> str:
    """Generate and bind a new correlation ID for the current context."""
    correlation_id = str(uuid.uuid4())
    correlation_id_var.set(correlation_id)
    return correlation_id


def bind_log_context(
    *,
    correlation_id: str | None = None,
    webhook_event_id: str | None = None,
    payment_id: str | None = None,
    recovery_case_id: str | None = None,
) -> None:
    """Bind optional structured logging context variables."""
    if correlation_id is not None:
        correlation_id_var.set(correlation_id)
    if webhook_event_id is not None:
        webhook_event_id_var.set(webhook_event_id)
    if payment_id is not None:
        payment_id_var.set(payment_id)
    if recovery_case_id is not None:
        recovery_case_id_var.set(recovery_case_id)


def clear_log_context() -> None:
    """Reset all structured logging context variables."""
    correlation_id_var.set(None)
    webhook_event_id_var.set(None)
    payment_id_var.set(None)
    recovery_case_id_var.set(None)
