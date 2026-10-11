"""Add durable purchase fields without rewriting existing Digit Matches rows.

Revision ID: 003_digitmatch_execution
Revises: 002_digitmatch
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_digitmatch_execution"
down_revision: Union[str, None] = "002_digitmatch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("dm_intents", "symbol"):
        op.add_column("dm_intents", sa.Column("symbol", sa.String(length=32), nullable=True))
    if not _has_column("dm_intents", "duration_ticks"):
        op.add_column("dm_intents", sa.Column("duration_ticks", sa.Integer(), nullable=True))
    if not _has_column("dm_intents", "feature_epoch"):
        op.add_column("dm_intents", sa.Column("feature_epoch", sa.Integer(), nullable=True))
    if not _has_column("dm_intents", "mode"):
        op.add_column("dm_intents", sa.Column("mode", sa.String(length=32), nullable=True))
    if not _has_column("dm_models", "execution_eligible"):
        op.add_column(
            "dm_models",
            sa.Column("execution_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    pass
