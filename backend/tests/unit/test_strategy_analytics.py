"""Unit tests for Strategy Analytics Comparison Engine."""

import pytest
from app.services.analytics.comparison import StrategyAnalyticsEngine


@pytest.mark.asyncio
async def test_analytics_fallback_benchmark(db_session):
    """Verify default benchmark metrics when database is empty."""
    engine = StrategyAnalyticsEngine(db_session)
    report = await engine.generate_comparison()

    assert report.is_live_data is False
    assert report.phoenix_ai.recovery_rate_pct > report.baseline.recovery_rate_pct
    assert report.revenue_lift_paise > 0
    assert report.retries_avoided_count > 0
