"""Wave 1 schema: webhook events, recovery cases, audit logs."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Wave 1 tables per DATABASE.md."""
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        "raw_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature", sa.String(length=256), nullable=False),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("event_id", name="uq_raw_webhook_events_event_id"),
    )
    op.create_index("idx_raw_webhook_entity_id", "raw_webhook_events", ["entity_id"])
    op.create_index("idx_raw_webhook_event_type", "raw_webhook_events", ["event_type"])

    op.create_table(
        "recovery_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("customer_phone", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DETECTED"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=128), nullable=True),
        sa.Column("failure_telemetry", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_recovered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recovered_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("payment_id", name="uq_recovery_cases_payment_id"),
    )
    op.create_index("idx_recovery_cases_status", "recovery_cases", ["status"])
    op.create_index("idx_recovery_cases_order_id", "recovery_cases", ["order_id"])
    op.create_index("idx_recovery_cases_customer_email", "recovery_cases", ["customer_email"])
    op.create_index("idx_recovery_cases_created_at", "recovery_cases", ["created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("context_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["recovery_cases.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_audit_logs_case_id", "audit_logs", ["case_id"])
    op.create_index("idx_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    """Drop Wave 1 tables."""
    op.drop_index("idx_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_case_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("idx_recovery_cases_created_at", table_name="recovery_cases")
    op.drop_index("idx_recovery_cases_customer_email", table_name="recovery_cases")
    op.drop_index("idx_recovery_cases_order_id", table_name="recovery_cases")
    op.drop_index("idx_recovery_cases_status", table_name="recovery_cases")
    op.drop_table("recovery_cases")
    op.drop_index("idx_raw_webhook_event_type", table_name="raw_webhook_events")
    op.drop_index("idx_raw_webhook_entity_id", table_name="raw_webhook_events")
    op.drop_table("raw_webhook_events")
