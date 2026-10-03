"""
Contract settings model — stores user-confirmed contract configuration.
Alerts remain disabled until a valid confirmed settings row exists.
"""

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base


class ContractSettings(Base):
    __tablename__ = "contract_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # --- Instrument ---
    symbol = Column(String(32), nullable=False, comment="e.g. R_100 or R_100_1s")
    display_name = Column(String(128), nullable=True, comment="Human-readable name from API")
    market = Column(String(64), nullable=True, comment="e.g. synthetic_index")

    # --- Contract Type ---
    contract_type = Column(String(32), nullable=False, default="ONETOUCH")
    duration_value = Column(Integer, nullable=False, default=9)
    duration_unit = Column(String(4), nullable=False, default="m", comment="m=minutes, s=seconds, etc.")
    duration_seconds = Column(Integer, nullable=False, default=540)

    # --- Barrier Configuration ---
    barrier_input = Column(String(32), nullable=False, comment="User's input e.g. '+0.09'")
    barrier_type = Column(String(16), nullable=False, default="relative",
                          comment="relative or absolute")
    barrier_direction = Column(String(8), nullable=False, default="both",
                               comment="upper, lower, or both")
    barrier_unit_description = Column(Text, nullable=True,
                                       comment="Describes what barrier value means")

    # --- Pricing (from confirmed quote) ---
    currency = Column(String(8), default="USD")
    reference_purchase_price = Column(Float, nullable=True, comment="Reference stake")
    reference_payout = Column(Float, nullable=True, comment="Reference total payout")
    reference_net_profit_pct = Column(Float, nullable=True, comment="Net % return on win")

    # --- Confirmation Status ---
    is_confirmed = Column(Boolean, default=False, nullable=False,
                          comment="User must confirm before alerts are enabled")
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by = Column(String(64), nullable=True, comment="Identifier of who confirmed")

    # --- Discovery Results ---
    api_contracts_for_raw = Column(Text, nullable=True,
                                    comment="Raw JSON from contracts_for for audit")
    api_active_symbols_raw = Column(Text, nullable=True,
                                     comment="Raw JSON from active_symbols for audit")

    # --- Runtime alert control ---
    alerts_paused = Column(Boolean, default=False, nullable=False,
                           comment="When true, validated auto-alerts are suppressed")
    pause_reason = Column(Text, nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)

    # --- Notes ---
    notes = Column(Text, nullable=True)

    def __repr__(self):
        return (
            f"<ContractSettings(symbol={self.symbol}, type={self.contract_type}, "
            f"barrier={self.barrier_input}, confirmed={self.is_confirmed})>"
        )
