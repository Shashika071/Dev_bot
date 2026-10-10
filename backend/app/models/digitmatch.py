"""Digit Matches tables. Separate from the touch-signal schema."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base


class DmTick(Base):
    __tablename__ = "dm_ticks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    broker_epoch = Column(Integer, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    quote_wire = Column(Text, nullable=False)
    quote_text = Column(String(64), nullable=False)
    precision = Column(Integer, nullable=True)
    last_digit = Column(String(1), nullable=True)
    digit_value = Column(Integer, nullable=True)
    source = Column(String(16), nullable=False)
    ingestion_id = Column(String(64), nullable=False, index=True)
    broker_tick_id = Column(String(64), nullable=True)
    dedup_key = Column(String(160), nullable=False)
    quality_flags = Column(Text, nullable=False, default="")
    pip_size_raw = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_dm_ticks_dedup"),
        Index("ix_dm_ticks_symbol_epoch", "symbol", "broker_epoch"),
    )


class DmIngestJob(Base):
    __tablename__ = "dm_ingest_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ingestion_id = Column(String(64), nullable=False, unique=True)
    symbol = Column(String(32), nullable=False)
    target_ticks = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="queued")
    ticks_stored = Column(Integer, nullable=False, default=0)
    pages = Column(Integer, nullable=False, default=0)
    checkpoint_end = Column(String(32), nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    error = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DmDataset(Base):
    __tablename__ = "dm_datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    source = Column(String(32), nullable=False)
    sha256 = Column(String(64), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DmRuntime(Base):
    __tablename__ = "dm_runtime"

    id = Column(Integer, primary_key=True)
    mode = Column(String(32), nullable=False, default="observe")
    paused = Column(Boolean, nullable=False, default=False)
    emergency_stop = Column(Boolean, nullable=False, default=False)
    pause_reason = Column(Text, nullable=True)
    stake = Column(Float, nullable=False, default=1.0)
    cooldown_seconds = Column(Integer, nullable=False, default=30)
    max_trades_per_day = Column(Integer, nullable=False, default=50)
    daily_loss_limit = Column(Float, nullable=False, default=20.0)
    daily_profit_stop = Column(Float, nullable=False, default=20.0)
    reset_timezone = Column(String(64), nullable=False, default="Asia/Colombo")
    margin = Column(Float, nullable=False, default=0.02)
    resolved_symbol = Column(String(32), nullable=True)
    resolved_display = Column(String(128), nullable=True)
    matches_legacy_symbol = Column(Boolean, nullable=True)
    connection_status = Column(String(32), nullable=False, default="offline")
    auth_status = Column(String(32), nullable=False, default="unconfigured")
    demo_verified = Column(Boolean, nullable=False, default=False)
    loginid = Column(String(64), nullable=True)
    currency = Column(String(16), nullable=True)
    balance = Column(Float, nullable=True)
    last_tick_epoch = Column(Integer, nullable=True)
    last_tick_received_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    active_model_id = Column(Integer, nullable=True)
    uncertain_block = Column(Boolean, nullable=False, default=False)
    uncertain_reason = Column(Text, nullable=True)
    instrument_error = Column(Text, nullable=True)
    contract_error = Column(Text, nullable=True)
    broker_min_stake = Column(Float, nullable=True)
    contract_ready = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DmLock(Base):
    __tablename__ = "dm_locks"

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)


class DmIntent(Base):
    __tablename__ = "dm_intents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_key = Column(String(64), nullable=False, unique=True)
    status = Column(String(32), nullable=False)
    digit = Column(Integer, nullable=False)
    stake = Column(Float, nullable=False)
    proposal_id = Column(String(128), nullable=True)
    ask_price = Column(Float, nullable=True)
    total_payout = Column(Float, nullable=True)
    calibrated_probability = Column(Float, nullable=True)
    contract_id = Column(String(64), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DmContract(Base):
    __tablename__ = "dm_contracts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    intent_id = Column(Integer, nullable=True, index=True)
    broker_contract_id = Column(String(64), nullable=False, unique=True)
    digit = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="open")
    buy_price = Column(Float, nullable=True)
    total_payout = Column(Float, nullable=True)
    profit = Column(Float, nullable=True)
    entry_spot = Column(String(64), nullable=True)
    exit_spot = Column(String(64), nullable=True)
    purchase_time = Column(DateTime(timezone=True), nullable=True)
    sell_time = Column(DateTime(timezone=True), nullable=True)
    longcode = Column(Text, nullable=True)
    raw_json = Column(Text, nullable=True)
    mode = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DmDecision(Base):
    __tablename__ = "dm_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    mode = Column(String(32), nullable=False)
    action = Column(String(16), nullable=False)
    reason = Column(Text, nullable=False)
    digit = Column(Integer, nullable=True)
    probabilities_json = Column(Text, nullable=True)
    break_even = Column(Float, nullable=True)
    expected_value = Column(Float, nullable=True)
    ask_price = Column(Float, nullable=True)
    total_payout = Column(Float, nullable=True)
    tick_epoch = Column(Integer, nullable=True)


class DmAudit(Base):
    __tablename__ = "dm_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    event = Column(String(64), nullable=False)
    detail = Column(Text, nullable=False, default="")


class DmModel(Base):
    __tablename__ = "dm_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    name = Column(String(64), nullable=False)
    schema = Column(String(64), nullable=False)
    seed = Column(Integer, nullable=False)
    path = Column(Text, nullable=False)
    checksum = Column(String(64), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    dataset_version = Column(String(64), nullable=False)
    time_start = Column(Integer, nullable=True)
    time_end = Column(Integer, nullable=True)
    params_json = Column(Text, nullable=False, default="{}")
    metrics_json = Column(Text, nullable=False, default="{}")
    calibration = Column(String(64), nullable=False)
    dependency_versions = Column(Text, nullable=False, default="{}")


class DmTrainJob(Base):
    __tablename__ = "dm_train_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(24), nullable=False, default="queued")
    progress = Column(Text, nullable=False, default="")
    error = Column(Text, nullable=True)
    model_id = Column(Integer, nullable=True)
    enable_mlp = Column(Boolean, nullable=False, default=False)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
