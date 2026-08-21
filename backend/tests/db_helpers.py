"""Test database helpers for SQLite compatibility."""

from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def adapt_metadata_for_sqlite(metadata) -> None:
    """No-op: models use cross-dialect Uuid and JSON types."""
    return metadata


def sqlite_insert_for_tests():
    """Return SQLite-compatible INSERT for repository upserts in tests."""
    return sqlite_insert
