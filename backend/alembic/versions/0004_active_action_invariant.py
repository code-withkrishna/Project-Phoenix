"""Add database invariant for one active recovery action per case."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_active_action_invariant"
down_revision: Union[str, None] = "0003_recovery_actions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_recovery_actions_active_case",
        "recovery_actions",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'ISSUED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_recovery_actions_active_case", table_name="recovery_actions")
