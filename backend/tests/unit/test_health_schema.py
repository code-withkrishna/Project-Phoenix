"""Unit tests for health response schema."""

from app.schemas.health import HealthResponse


def test_health_response_schema():
    """HealthResponse serializes to the contract shape."""
    response = HealthResponse(status="ok", service="phoenix-backend")

    assert response.model_dump() == {
        "status": "ok",
        "service": "phoenix-backend",
    }
