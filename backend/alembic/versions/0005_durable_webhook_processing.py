"""Durable webhook processing state.

Revision ID: 0005_durable_webhook_processing
Revises: 0004_active_action_invariant
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_durable_webhook_processing"
down_revision: Union[str, None] = "0004_active_action_invariant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add durable worker lease and retry metadata to webhook events."""
    op.add_column(
        "raw_webhook_events",
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "raw_webhook_events",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "raw_webhook_events",
        sa.Column("last_processing_error", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    """Remove durable worker metadata."""
    op.drop_column("raw_webhook_events", "last_processing_error")
    op.drop_column("raw_webhook_events", "processing_started_at")
    op.drop_column("raw_webhook_events", "processing_attempts")
