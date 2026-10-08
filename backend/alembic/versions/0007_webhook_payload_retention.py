"""Minimize retained webhook payload data after processing."""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_webhook_payload_retention"
down_revision: Union[str, None] = "0006_audit_log_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("raw_webhook_events", sa.Column("payload_redacted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("raw_webhook_events", sa.Column("payload_sha256", sa.String(length=64), nullable=True))

def downgrade() -> None:
    op.drop_column("raw_webhook_events", "payload_sha256")
    op.drop_column("raw_webhook_events", "payload_redacted_at")
