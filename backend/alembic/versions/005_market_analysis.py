"""Market candles, analysis history, forecasts, annotations, and model task ids.

Revision ID: 005_market_analysis
Revises: 004_digitmatch_recovery
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_market_analysis"
down_revision: Union[str, None] = "004_digitmatch_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_table("market_candles"):
        op.create_table(
            "market_candles",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("interval", sa.String(8), nullable=False),
            sa.Column("epoch", sa.BigInteger(), nullable=False),
            sa.Column("open", sa.Float(), nullable=False),
            sa.Column("high", sa.Float(), nullable=False),
            sa.Column("low", sa.Float(), nullable=False),
            sa.Column("close", sa.Float(), nullable=False),
            sa.Column("tick_count", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(32), nullable=False, server_default="tick_aggregate"),
            sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("symbol", "interval", "epoch", name="ix_market_candles_key"),
        )
    if not _has_table("analysis_events"):
        op.create_table(
            "analysis_events",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("interval", sa.String(8), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("epoch", sa.BigInteger(), nullable=False),
            sa.Column("end_epoch", sa.BigInteger(), nullable=True),
            sa.Column("geometry_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("evidence", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not _has_table("forecast_snapshots"):
        op.create_table(
            "forecast_snapshots",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("task_id", sa.String(32), nullable=False),
            sa.Column("epoch", sa.BigInteger(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not _has_table("chart_annotations"):
        op.create_table(
            "chart_annotations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("interval", sa.String(8), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("geometry_json", sa.Text(), nullable=False),
            sa.Column("style_json", sa.Text(), nullable=True),
            sa.Column("label", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if _has_table("model_versions") and not _has_column("model_versions", "task_id"):
        op.add_column("model_versions", sa.Column("task_id", sa.String(32), nullable=True))
    if _has_table("model_versions"):
        op.alter_column(
            "model_versions",
            "direction",
            existing_type=sa.String(8),
            type_=sa.String(32),
            existing_nullable=True,
        )


def downgrade() -> None:
    pass
