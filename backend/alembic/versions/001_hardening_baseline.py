"""Hardening baseline — additive columns for model registry / pause / ops.

Revision ID: 001_hardening
Revises:
Create Date: 2026-10-03

Safe for existing VPS pgdata: uses IF NOT EXISTS / try-add patterns.
create_all still bootstraps brand-new DBs; this migration upgrades older ones.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "001_hardening"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    # Ensure core tables exist (noop if already created by create_all)
    from app.database import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    # model_versions additive columns
    adds = [
        ("model_versions", "direction", sa.Column("direction", sa.String(8), nullable=True)),
        ("model_versions", "symbol", sa.Column("symbol", sa.String(32), nullable=True)),
        ("model_versions", "barrier_distance", sa.Column("barrier_distance", sa.Float(), nullable=True)),
        ("model_versions", "duration_seconds", sa.Column("duration_seconds", sa.Integer(), nullable=True)),
        ("model_versions", "n_cal_samples", sa.Column("n_cal_samples", sa.Integer(), nullable=True)),
        ("model_versions", "sampling_interval_seconds", sa.Column("sampling_interval_seconds", sa.Integer(), nullable=True)),
        ("model_versions", "effective_sample_count", sa.Column("effective_sample_count", sa.Integer(), nullable=True)),
        ("model_versions", "alerts_paused", sa.Column("alerts_paused", sa.Boolean(), server_default=sa.false())),
        ("model_versions", "pause_reason", sa.Column("pause_reason", sa.Text(), nullable=True)),
        ("model_versions", "metadata_json", sa.Column("metadata_json", sa.Text(), nullable=True)),
        ("contract_settings", "alerts_paused", sa.Column("alerts_paused", sa.Boolean(), server_default=sa.false(), nullable=False)),
        ("contract_settings", "pause_reason", sa.Column("pause_reason", sa.Text(), nullable=True)),
        ("contract_settings", "paused_at", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True)),
    ]
    for table, col, column in adds:
        if _has_table(table) and not _has_column(table, col):
            op.add_column(table, column)

    if _has_table("model_versions"):
        try:
            op.create_index("ix_model_versions_direction", "model_versions", ["direction"], unique=False)
        except Exception:
            pass
        try:
            op.create_index("ix_model_versions_symbol", "model_versions", ["symbol"], unique=False)
        except Exception:
            pass
        try:
            op.create_index("ix_model_versions_version_tag", "model_versions", ["version_tag"], unique=True)
        except Exception:
            pass

    if not _has_table("ops_snapshots"):
        op.create_table(
            "ops_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("symbol", sa.String(32), nullable=True),
            sa.Column("tick_count", sa.BigInteger(), nullable=True),
            sa.Column("tick_oldest_epoch", sa.BigInteger(), nullable=True),
            sa.Column("tick_newest_epoch", sa.BigInteger(), nullable=True),
            sa.Column("tick_gap_count", sa.Integer(), nullable=True),
            sa.Column("quotes_collected", sa.Integer(), nullable=True),
            sa.Column("deriv_connected", sa.Integer(), nullable=True),
            sa.Column("active_model_upper", sa.String(64), nullable=True),
            sa.Column("active_model_lower", sa.String(64), nullable=True),
            sa.Column("alerts_paused", sa.Integer(), nullable=True),
            sa.Column("pause_reason", sa.Text(), nullable=True),
            sa.Column("resolved_7d", sa.Integer(), nullable=True),
            sa.Column("win_rate_7d", sa.Float(), nullable=True),
            sa.Column("mean_breakeven_7d", sa.Float(), nullable=True),
            sa.Column("db_size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
        )
        op.create_index("ix_ops_snapshots_created_at", "ops_snapshots", ["created_at"])
        op.create_index("ix_ops_snapshots_symbol", "ops_snapshots", ["symbol"])


def downgrade() -> None:
    # Non-destructive downgrade: leave additive columns in place for safety.
    pass
