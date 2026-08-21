"""Unit tests for database foundation."""

from app.core.database import Base, get_engine, get_session_factory, init_db


def test_base_metadata_has_wave1_tables():
    """Wave 1 ORM models are registered on metadata."""
    table_names = set(Base.metadata.tables.keys())
    assert table_names == {
        "raw_webhook_events",
        "recovery_cases",
        "audit_logs",
    }


def test_init_db_exposes_engine_and_session_factory(settings):
    """Database initialization wires engine and session factory."""
    init_db(settings.database_url)

    engine = get_engine()
    session_factory = get_session_factory()

    assert "://" in engine.url.render_as_string(hide_password=True)
    assert session_factory is not None
