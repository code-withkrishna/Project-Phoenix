"""Merchant Copilot REST endpoints."""

from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.copilot.engine import MerchantCopilotEngine

router = APIRouter(prefix="/copilot", tags=["copilot"])


class CopilotQueryRequest(BaseModel):
    """Natural language query from merchant."""

    query: str = Field(..., min_length=1, max_length=500, description="Merchant natural language question")


class CopilotQueryResponse(BaseModel):
    """Structured response containing natural text and tool data."""

    intent: str
    reply_text: str
    data_payload: dict[str, Any]
    suggested_actions: list[str]


@router.post("/query", response_model=CopilotQueryResponse)
async def query_merchant_copilot(
    req: CopilotQueryRequest,
    session: AsyncSession = Depends(get_db),
) -> CopilotQueryResponse:
    """Query the tool-grounded Merchant Copilot."""
    engine = MerchantCopilotEngine(session)
    response = await engine.query(req.query)
    return CopilotQueryResponse(
        intent=response.intent,
        reply_text=response.reply_text,
        data_payload=response.data_payload,
        suggested_actions=response.suggested_actions,
    )
