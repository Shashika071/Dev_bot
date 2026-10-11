"""Store the loss amount the next demo win is sized to cover.

Revision ID: 004_digitmatch_recovery
Revises: 003_digitmatch_execution
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_digitmatch_recovery"
down_revision: Union[str, None] = "003_digitmatch_execution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("dm_runtime", "recovery_loss"):
        op.add_column(
            "dm_runtime",
            sa.Column("recovery_loss", sa.Float(), nullable=False, server_default="0"),
        )
    if not _has_column("dm_runtime", "win_profit_target"):
        op.add_column(
            "dm_runtime",
            sa.Column("win_profit_target", sa.Float(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    pass
