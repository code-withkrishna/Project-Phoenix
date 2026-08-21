"""Version 1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1.recovery_cases import router as recovery_cases_router
from app.api.v1.webhooks import router as webhooks_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(webhooks_router)
api_v1_router.include_router(recovery_cases_router)
