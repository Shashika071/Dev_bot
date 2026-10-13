"""Persisted market candles, analysis events, forecasts, and drawings."""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class MarketCandle(Base):
    __tablename__ = "market_candles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False)
    interval = Column(String(8), nullable=False)
    epoch = Column(BigInteger, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    tick_count = Column(Integer, nullable=True)
    source = Column(String(32), nullable=False, default="tick_aggregate")
    is_closed = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_market_candles_key", "symbol", "interval", "epoch", unique=True),
    )


class AnalysisEvent(Base):
    __tablename__ = "analysis_events"

    id = Column(String(64), primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    interval = Column(String(8), nullable=False)
    event_type = Column(String(64), nullable=False)
    direction = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="active")
    confidence = Column(Float, nullable=False, default=0)
    epoch = Column(BigInteger, nullable=False)
    end_epoch = Column(BigInteger, nullable=True)
    geometry_json = Column(Text, nullable=False, default="{}")
    evidence = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    task_id = Column(String(32), nullable=False)
    epoch = Column(BigInteger, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChartAnnotation(Base):
    __tablename__ = "chart_annotations"

    id = Column(String(36), primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    interval = Column(String(8), nullable=False)
    kind = Column(String(24), nullable=False)
    geometry_json = Column(Text, nullable=False)
    style_json = Column(Text, nullable=True)
    label = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
