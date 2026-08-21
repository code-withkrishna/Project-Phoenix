"""Async SQLAlchemy engine and session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_current_url: str | None = None


def _engine_connect_args(database_url: str) -> dict:
    """Return dialect-specific connect args for the database URL."""
    if database_url.startswith("sqlite"):
        return {"uri": True}
    return {}


def init_db(database_url: str) -> None:
    """Initialize the async engine and session factory."""
    global _engine, _session_factory, _current_url

    if _engine is not None and _current_url == database_url:
        return

    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        connect_args=_engine_connect_args(database_url),
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _current_url = database_url


def bind_engine(engine: AsyncEngine, database_url: str) -> None:
    """Bind an existing engine for app/test use without creating a new pool."""
    global _engine, _session_factory, _current_url

    _engine = engine
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _current_url = database_url


def get_engine() -> AsyncEngine:
    """Return the configured async engine."""
    if _engine is None:
        raise RuntimeError("Database has not been initialized. Call init_db() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the configured session factory."""
    if _session_factory is None:
        raise RuntimeError("Database has not been initialized. Call init_db() first.")
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _session_factory, _current_url

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _current_url = None


def reset_db_state() -> None:
    """Clear module-level DB state without disposing engines (for tests)."""
    global _engine, _session_factory, _current_url

    _engine = None
    _session_factory = None
    _current_url = None
