"""Shared API schema primitives."""

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base model with ORM attribute support."""

    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated list response."""

    total: int
    page: int
    page_size: int
    items: list[T]


class TimestampedModel(ORMModel):
    """Mixin for created/updated timestamps."""

    created_at: datetime
    updated_at: datetime | None = None


class AuditLogEntry(ORMModel):
    """Audit log entry for case detail responses."""

    id: UUID
    case_id: UUID | None = None
    from_state: str | None = None
    to_state: str
    trigger: str
    actor: str
    context_metadata: dict = Field(default_factory=dict)
    created_at: datetime
