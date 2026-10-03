"""
Signal outcome model — records actual tick-verified outcomes for signals.
"""

from sqlalchemy import Column, BigInteger, Integer, Float, DateTime, String, Boolean, Text, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    signal_id = Column(String(32), ForeignKey("signals.signal_id"),
                       nullable=False, unique=True, index=True)

    # --- Outcome ---
    barrier_touched = Column(Boolean, nullable=True,
                              comment="True if barrier was touched during window")
    touch_time = Column(DateTime(timezone=True), nullable=True,
                        comment="First tick that touched/crossed barrier")
    touch_epoch = Column(BigInteger, nullable=True)
    touch_price = Column(Float, nullable=True, comment="Price that triggered touch")

    # --- Window ---
    window_start = Column(DateTime(timezone=True), nullable=True,
                          comment="Contract window start (entry time or signal time)")
    window_end = Column(DateTime(timezone=True), nullable=True,
                        comment="Contract window end (start + duration)")
    ticks_in_window = Column(Integer, nullable=True,
                              comment="Number of ticks observed during window")

    # --- Price Movement ---
    max_price_in_window = Column(Float, nullable=True)
    min_price_in_window = Column(Float, nullable=True)
    price_at_start = Column(Float, nullable=True)
    price_at_end = Column(Float, nullable=True)
    max_favorable_excursion = Column(Float, nullable=True,
                                      comment="Closest approach to barrier")

    # --- Financial Outcome ---
    # Only populated when a real quote was available
    purchase_price = Column(Float, nullable=True)
    payout = Column(Float, nullable=True)
    net_pnl = Column(Float, nullable=True,
                     comment="payout - purchase_price if won, -purchase_price if lost")
    is_hypothetical = Column(Boolean, default=True,
                              comment="True if no manual entry was recorded")

    # --- Evaluation ---
    evaluation_method = Column(String(32), default="tick_verified",
                                comment="tick_verified or candle_estimated")
    evaluated_at = Column(DateTime(timezone=True), server_default=func.now())
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return (
            f"<SignalOutcome(signal={self.signal_id}, touched={self.barrier_touched}, "
            f"pnl={self.net_pnl})>"
        )
