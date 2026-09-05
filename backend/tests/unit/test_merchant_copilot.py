"""Unit tests for Merchant Copilot Engine."""

import pytest
from app.services.copilot.engine import MerchantCopilotEngine


@pytest.mark.asyncio
async def test_copilot_revenue_query(db_session):
    """Verify natural language query for revenue metrics."""
    engine = MerchantCopilotEngine(db_session)
    res = await engine.query("How much revenue did we recover?")

    assert res.intent == "REVENUE_OVERVIEW"
    assert "Revenue Recovery Overview" in res.reply_text
    assert "recovered_amount_inr" in res.data_payload


@pytest.mark.asyncio
async def test_copilot_pending_reviews_query(db_session):
    """Verify natural language query for pending HITL reviews."""
    engine = MerchantCopilotEngine(db_session)
    res = await engine.query("Show high value cases needing review")

    assert res.intent == "PENDING_REVIEWS"
    assert "pending_count" in res.data_payload


@pytest.mark.asyncio
async def test_copilot_strategy_query(db_session):
    """Verify natural language query for ROI & strategy comparison."""
    engine = MerchantCopilotEngine(db_session)
    res = await engine.query("What is our recovery ROI vs baseline strategy?")

    assert res.intent == "STRATEGY_COMPARISON"
    assert "Phoenix AI vs. Naive Baseline Strategy" in res.reply_text
