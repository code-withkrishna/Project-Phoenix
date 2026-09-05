"""Analytics REST endpoints."""

from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.analytics.comparison import StrategyAnalyticsEngine

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/comparison")
async def get_strategy_comparison(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get head-to-head performance comparison between Naive Baseline and Phoenix AI Orchestrator."""
    engine = StrategyAnalyticsEngine(session)
    report = await engine.generate_comparison()
    return report.to_dict()
