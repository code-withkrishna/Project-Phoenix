"""Append-only and tamper-evident audit log enforcement.

Revision ID: 0005_audit_log_integrity
Revises: 0004_durable_webhook_processing
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_audit_log_integrity"
down_revision: Union[str, None] = "0004_durable_webhook_processing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add integrity hashes and prevent audit-row mutation."""
    op.add_column(
        "audit_logs",
        sa.Column("integrity_hash", sa.String(length=64), nullable=True),
    )

    op.execute(
        """
        UPDATE audit_logs
        SET integrity_hash = encode(
            digest(
                concat_ws(
                    '|',
                    id::text,
                    coalesce(case_id::text, ''),
                    coalesce(from_state, ''),
                    to_state,
                    trigger,
                    actor
                ),
                'sha256'
            ),
            'hex'
        )
        """
    )

    op.alter_column("audit_logs", "integrity_hash", nullable=False)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )


def downgrade() -> None:
    """Remove audit-row mutation protection."""
    op.execute("DROP TRIGGER IF EXISTS audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation()")
    op.drop_column("audit_logs", "integrity_hash")
