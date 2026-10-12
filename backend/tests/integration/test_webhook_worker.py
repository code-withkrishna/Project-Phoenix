"""Regression coverage for the durable webhook worker lifecycle."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.webhooks.dispatcher import run_webhook_worker


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_worker_calls_session_factory_and_stops_cleanly(monkeypatch):
    """The worker must instantiate the sessionmaker before entering its context."""
    from app.core import database
    from app.repositories import webhook_events

    stop_event = asyncio.Event()
    settings = SimpleNamespace(
        database_url="postgresql+asyncpg://unused",
        webhook_worker_batch_size=10,
        webhook_worker_interval_seconds=0.5,
    )
    factory_calls = 0

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return _SessionContext()

    monkeypatch.setattr(database, "init_db", lambda _url: None)
    monkeypatch.setattr(database, "get_session_factory", lambda: session_factory)

    async def no_pending_events(self, _batch_size):
        stop_event.set()
        return []

    monkeypatch.setattr(webhook_events.WebhookEventRepository, "list_pending_event_ids", no_pending_events)

    await asyncio.wait_for(run_webhook_worker(settings, stop_event), timeout=2)
    assert factory_calls == 1
