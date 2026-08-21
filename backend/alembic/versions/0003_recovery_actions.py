"""Wave 3 schema: recovery_actions table."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_recovery_actions"
down_revision: Union[str, None] = "0002_ai_diagnoses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create recovery_actions table per DATABASE.md and Wave 3 specification."""
    op.create_table(
        "recovery_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False, server_default="CREATE_PAYMENT_LINK"),
        sa.Column("reference_id", sa.String(length=40), nullable=False),
        sa.Column("payment_link_id", sa.String(length=128), nullable=True),
        sa.Column("payment_link_url", sa.String(length=512), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["recovery_cases.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("reference_id", name="uq_recovery_actions_reference_id"),
        sa.UniqueConstraint("payment_link_id", name="uq_recovery_actions_payment_link_id"),
    )
    op.create_index("idx_recovery_actions_case_id", "recovery_actions", ["case_id"])
    op.create_index("idx_recovery_actions_ref_id", "recovery_actions", ["reference_id"])
    op.create_index("idx_recovery_actions_plink_id", "recovery_actions", ["payment_link_id"])
    op.create_index("idx_recovery_actions_status", "recovery_actions", ["status"])


def downgrade() -> None:
    """Drop recovery_actions table."""
    op.drop_index("idx_recovery_actions_status", table_name="recovery_actions")
    op.drop_index("idx_recovery_actions_plink_id", table_name="recovery_actions")
    op.drop_index("idx_recovery_actions_ref_id", table_name="recovery_actions")
    op.drop_index("idx_recovery_actions_case_id", table_name="recovery_actions")
    op.drop_table("recovery_actions")
