"""Digit Matches schema for databases that already ran the touch-bot migration.

Revision ID: 002_digitmatch
Revises: 001_hardening
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002_digitmatch"
down_revision: Union[str, None] = "001_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    pass
