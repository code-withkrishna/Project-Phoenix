"""Health check response schema."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str = Field(examples=["ok"])
    service: str = Field(examples=["phoenix-backend"])
