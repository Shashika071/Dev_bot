"""Periodic ops / health snapshots for multi-week monitoring."""

from sqlalchemy import Column, Integer, Float, DateTime, String, Text, BigInteger
from sqlalchemy.sql import func
from app.database import Base


class OpsSnapshot(Base):
    __tablename__ = "ops_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    symbol = Column(String(32), nullable=True, index=True)
    tick_count = Column(BigInteger, nullable=True)
    tick_oldest_epoch = Column(BigInteger, nullable=True)
    tick_newest_epoch = Column(BigInteger, nullable=True)
    tick_gap_count = Column(Integer, nullable=True)
    quotes_collected = Column(Integer, nullable=True)
    deriv_connected = Column(Integer, nullable=True, comment="1/0")

    active_model_upper = Column(String(64), nullable=True)
    active_model_lower = Column(String(64), nullable=True)
    alerts_paused = Column(Integer, nullable=True, comment="1/0")
    pause_reason = Column(Text, nullable=True)

    resolved_7d = Column(Integer, nullable=True)
    win_rate_7d = Column(Float, nullable=True)
    mean_breakeven_7d = Column(Float, nullable=True)
    db_size_bytes = Column(BigInteger, nullable=True)
    notes = Column(Text, nullable=True)
