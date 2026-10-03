"""
Quote model — stores Touch contract quotes (proposals) without purchasing.
Records pricing for EV calculations and backtesting with real quotes.
"""

from sqlalchemy import Column, BigInteger, Integer, Float, DateTime, String, Text, Index
from sqlalchemy.sql import func
from app.database import Base


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # --- Contract Specification ---
    symbol = Column(String(32), nullable=False, index=True)
    contract_type = Column(String(32), nullable=False, comment="ONETOUCH")
    duration_value = Column(Integer, nullable=False)
    duration_unit = Column(String(4), nullable=False)
    barrier_input = Column(String(32), nullable=False, comment="e.g. +0.09")
    barrier_direction = Column(String(8), nullable=False, comment="upper or lower")

    # --- Pricing ---
    currency = Column(String(8), default="USD")
    ask_price = Column(Float, nullable=False, comment="Purchase price (stake)")
    payout = Column(Float, nullable=False, comment="Total payout if touch")
    basis = Column(String(16), default="payout", comment="payout or stake basis")
    amount_requested = Column(Float, nullable=True, comment="Amount used in proposal request")

    # --- Derived ---
    net_profit = Column(Float, nullable=True, comment="payout - ask_price")
    net_profit_pct = Column(Float, nullable=True, comment="net_profit / ask_price * 100")
    breakeven_prob = Column(Float, nullable=True, comment="ask_price / payout")

    # --- Spot / Barrier ---
    spot = Column(Float, nullable=True, comment="Current spot price at quote time")
    spot_time = Column(BigInteger, nullable=True, comment="Spot epoch")
    barrier_resolved = Column(Float, nullable=True,
                               comment="Absolute barrier value resolved by API")

    # --- Timestamps ---
    quote_epoch = Column(BigInteger, nullable=True, comment="Proposal epoch from API")
    quote_time = Column(DateTime(timezone=True), nullable=True,
                        comment="Proposal timestamp as datetime")
    received_at = Column(DateTime(timezone=True), server_default=func.now())

    # --- API Response ---
    proposal_id = Column(String(64), nullable=True, comment="Deriv proposal ID")
    raw_response = Column(Text, nullable=True, comment="Full JSON for audit")

    __table_args__ = (
        Index("ix_quotes_symbol_time", "symbol", "quote_time"),
        Index("ix_quotes_symbol_direction", "symbol", "barrier_direction"),
    )

    def __repr__(self):
        return (
            f"<Quote(symbol={self.symbol}, type={self.contract_type}, "
            f"barrier={self.barrier_input}, ask={self.ask_price}, payout={self.payout})>"
        )
