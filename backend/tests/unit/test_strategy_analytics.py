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

@pytest.mark.asyncio
async def test_analytics_labels_live_phoenix_and_simulated_baseline(
    db_session,
):
    engine = StrategyAnalyticsEngine(db_session)
    report = await engine.generate_comparison()

    payload = report.to_dict()["comparison"]
    assert payload["is_live_data"] is False
    assert payload["phoenix_data_source"] == "LIVE_PERSISTED_DATABASE"
    assert payload["baseline_data_source"] == "SIMULATED_BENCHMARK_MODEL"
    assert payload["data_source"] == "SIMULATED_BENCHMARK_MODEL"
