"""
Tick data model — stores every tick from Deriv with timestamps and latency.
"""

from sqlalchemy import Column, BigInteger, Float, DateTime, String, Integer, Index
from sqlalchemy.sql import func
from app.database import Base


class Tick(Base):
    __tablename__ = "ticks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    symbol = Column(String(32), nullable=False, index=True)
    epoch = Column(BigInteger, nullable=False, comment="Deriv server epoch (seconds)")
    tick_time = Column(DateTime(timezone=True), nullable=False, comment="Server timestamp as datetime")
    quote = Column(Float, nullable=False, comment="Price quote")
    ask = Column(Float, nullable=True)
    bid = Column(Float, nullable=True)

    # Latency monitoring
    received_at = Column(DateTime(timezone=True), server_default=func.now(),
                         comment="Local receive timestamp")
    latency_ms = Column(Integer, nullable=True,
                        comment="Estimated latency: received_at - tick_time in ms")

    # Deduplication
    pip_size = Column(Float, nullable=True, comment="Instrument pip size from API")

    # Data quality flags
    is_gap = Column(Integer, default=0, comment="1 if a gap was detected before this tick")

    __table_args__ = (
        Index("ix_ticks_symbol_epoch", "symbol", "epoch", unique=True),
        Index("ix_ticks_symbol_time", "symbol", "tick_time"),
    )

    def __repr__(self):
        return f"<Tick(symbol={self.symbol}, epoch={self.epoch}, quote={self.quote})>"
