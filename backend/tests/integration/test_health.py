"""Integration tests for health endpoint."""

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    """GET /health returns the documented liveness payload."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "phoenix-backend",
    }
