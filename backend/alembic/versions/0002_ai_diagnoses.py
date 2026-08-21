"""Wave 2 schema: ai_diagnoses table."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_ai_diagnoses"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ai_diagnoses table per DATABASE.md."""
    op.create_table(
        "ai_diagnoses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("root_cause_category", sa.String(length=64), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("diagnostic_summary", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.String(length=64), nullable=False),
        sa.Column("suggested_expiry_minutes", sa.Integer(), nullable=True),
        sa.Column("suggested_customer_note", sa.Text(), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["recovery_cases.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_ai_diagnoses_case_id", "ai_diagnoses", ["case_id"])


def downgrade() -> None:
    """Drop ai_diagnoses table."""
    op.drop_index("idx_ai_diagnoses_case_id", table_name="ai_diagnoses")
    op.drop_table("ai_diagnoses")
