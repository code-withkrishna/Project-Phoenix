"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.health import router as health_router
from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.database import close_db, init_db
from app.core.logging import setup_logging
from app.services.webhooks.ingestion import WebhookIngestionError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    settings = get_settings()
    setup_logging(settings.log_level)
    init_db(settings.database_url)
    logger.info(
        "Application started: environment=%s service=%s",
        settings.environment,
        settings.app_name,
    )
    yield
    await close_db()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    application = FastAPI(
        title="Project Phoenix",
        description="Autonomous AI Revenue Recovery Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
        debug=settings.debug,
    )
    application.include_router(health_router)
    application.include_router(api_v1_router)

    @application.exception_handler(WebhookIngestionError)
    async def webhook_ingestion_error_handler(
        _request: Request,
        exc: WebhookIngestionError,
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    return application


app = create_app()
