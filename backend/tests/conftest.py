"""Shared pytest fixtures."""

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import Base, bind_engine, get_db, reset_db_state
from app.main import create_app
from tests.db_helpers import adapt_metadata_for_sqlite
from tests.fixtures.razorpay import TEST_WEBHOOK_SECRET


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "sqlite+aiosqlite:///file:phoenix_test?mode=memory&cache=shared&uri=true",
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Ensure settings are reloaded per test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Configure test environment variables."""
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fixture")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test_key_secret_fixture")
    monkeypatch.setenv("ENVIRONMENT", "test")


@pytest.fixture
def settings(test_env) -> Settings:
    """Return application settings for tests."""
    return get_settings()


@pytest.fixture(autouse=True)
def patch_sqlite_insert(monkeypatch, test_env):
    """Use SQLite-compatible INSERT for repository upserts in tests."""
    if not TEST_DATABASE_URL.startswith("sqlite"):
        yield
        return

    monkeypatch.setattr("app.repositories.webhook_events.insert", sqlite_insert)
    monkeypatch.setattr("app.repositories.recovery_cases.insert", sqlite_insert)
    yield


@pytest_asyncio.fixture
async def db_engine(test_env):
    """Create async engine and schema for tests."""
    adapt_metadata_for_sqlite(Base.metadata)
    connect_args = {"uri": True} if TEST_DATABASE_URL.startswith("sqlite") else {}
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args=connect_args,
    )
    if TEST_DATABASE_URL.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    bind_engine(engine, TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    reset_db_state()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()



@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session bound to the test engine."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with overridden DB dependency."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    bind_engine(db_engine, TEST_DATABASE_URL)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    application.dependency_overrides.clear()
