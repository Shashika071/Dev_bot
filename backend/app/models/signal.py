"""
Signal model — stores generated trading signals with full context.
Signals are for manual trading only; no auto-execution.
"""

import uuid
from sqlalchemy import Column, BigInteger, Integer, Float, DateTime, String, Text, Boolean, Index
from sqlalchemy.sql import func
from app.database import Base


def generate_signal_id():
    return f"SIG-{uuid.uuid4().hex[:12].upper()}"


class Signal(Base):
    __tablename__ = "signals"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    signal_id = Column(String(32), unique=True, nullable=False, default=generate_signal_id,
                       comment="Human-readable unique signal ID")

    # --- Timing ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    valid_until = Column(DateTime(timezone=True), nullable=True,
                         comment="Signal expiry time")
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidation_reason = Column(Text, nullable=True)

    # --- Instrument & Contract ---
    symbol = Column(String(32), nullable=False, index=True)
    contract_type = Column(String(32), nullable=False, default="ONETOUCH")
    duration_seconds = Column(Integer, nullable=False, default=540)
    touch_direction = Column(String(8), nullable=False, comment="upper or lower")

    # --- Barrier ---
    barrier_input = Column(String(32), nullable=False)
    barrier_unit = Column(String(32), nullable=True, comment="Description of barrier units")
    reference_price = Column(Float, nullable=True, comment="Spot at signal time")
    resolved_barrier = Column(Float, nullable=True, comment="Absolute target price")

    # --- Quote at Signal Time ---
    quote_id = Column(BigInteger, nullable=True, comment="FK to quotes table")
    quote_timestamp = Column(DateTime(timezone=True), nullable=True)
    purchase_price = Column(Float, nullable=True, comment="Quoted ask price")
    total_payout = Column(Float, nullable=True, comment="Quoted payout")
    breakeven_probability = Column(Float, nullable=True)

    # --- Model Prediction ---
    model_version_id = Column(Integer, nullable=True)
    calibrated_probability = Column(Float, nullable=True,
                                     comment="Calibrated touch probability from model")
    raw_probability = Column(Float, nullable=True, comment="Uncalibrated model output")
    ev_net = Column(Float, nullable=True, comment="Expected net value")
    ev_margin = Column(Float, nullable=True,
                       comment="Calibrated prob - breakeven prob")

    # --- Strategy ---
    strategy_name = Column(String(64), nullable=True)
    strategy_params = Column(Text, nullable=True, comment="JSON of strategy parameters")

    # --- Explanation ---
    explanation = Column(Text, nullable=True, comment="Short factual explanation")
    evidence_limitations = Column(Text, nullable=True)

    # --- Status ---
    status = Column(String(32), default="active",
                    comment="active, expired, invalidated, entered, resolved")
    is_validated = Column(Boolean, default=False,
                          comment="True only if model has demonstrated edge")

    # --- Daily Tracking ---
    day_date = Column(String(10), nullable=False, comment="YYYY-MM-DD in app timezone")
    day_sequence = Column(Integer, nullable=False, comment="1-based signal number for the day")

    # --- Manual Entry Recording ---
    manually_entered = Column(Boolean, default=False)
    entry_time = Column(DateTime(timezone=True), nullable=True)
    entry_price = Column(Float, nullable=True)
    entry_notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_signals_day", "day_date"),
        Index("ix_signals_status", "status"),
        Index("ix_signals_symbol_created", "symbol", "created_at"),
    )

    def __repr__(self):
        return (
            f"<Signal(id={self.signal_id}, symbol={self.symbol}, "
            f"direction={self.touch_direction}, prob={self.calibrated_probability})>"
        )
