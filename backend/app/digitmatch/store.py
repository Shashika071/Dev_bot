"""Database access for Digit Matches. The execution lock is a single row."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.digitmatch.risk import RiskSnapshot, trading_day_bounds
from app.digitmatch.validation import assess_tick
from app.models.digitmatch import (
    DmAudit,
    DmContract,
    DmDataset,
    DmDecision,
    DmIngestJob,
    DmIntent,
    DmLock,
    DmModel,
    DmRuntime,
    DmTick,
    DmTrainJob,
)

_ENGINE = None


def _contract_dict(row: DmContract) -> dict:
    return {
        "id": row.id,
        "broker_contract_id": row.broker_contract_id,
        "digit": row.digit,
        "status": row.status,
        "buy_price": row.buy_price,
        "total_payout": row.total_payout,
        "profit": row.profit,
        "entry_spot": row.entry_spot,
        "exit_spot": row.exit_spot,
        "mode": row.mode,
        "longcode": row.longcode,
        "purchase_time": None if row.purchase_time is None else row.purchase_time.isoformat(),
        "sell_time": None if row.sell_time is None else row.sell_time.isoformat(),
    }


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def sync_url(url: str | None = None) -> str:
    raw = url or settings.database_url
    if raw.startswith("postgresql+asyncpg://"):
        return raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return raw


def make_engine(url: str | None = None):
    global _ENGINE
    if url is None and _ENGINE is not None:
        return _ENGINE
    engine = create_engine(sync_url(url), future=True, pool_pre_ping=True)
    if url is None:
        _ENGINE = engine
    return engine


class SqlStore:
    def __init__(self, engine=None):
        self.engine = engine or make_engine()
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_schema(self) -> None:
        import app.models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def seed(self) -> None:
        with self.Session() as session:
            runtime = session.get(DmRuntime, 1)
            if runtime is None:
                session.add(
                    DmRuntime(
                        id=1,
                        mode="observe",
                        stake=settings.dm_stake,
                        cooldown_seconds=settings.dm_cooldown_seconds,
                        max_trades_per_day=settings.dm_max_trades_per_day,
                        daily_loss_limit=settings.dm_daily_loss_limit,
                        daily_profit_stop=settings.dm_daily_profit_stop,
                        reset_timezone=settings.dm_reset_timezone,
                        margin=settings.dm_default_margin,
                    )
                )
            if session.get(DmLock, 1) is None:
                session.add(DmLock(id=1))
            session.commit()

    def runtime(self) -> DmRuntime:
        self.seed()
        with self.Session() as session:
            row = session.get(DmRuntime, 1)
            session.expunge(row)
            return row

    def update_runtime(self, **values) -> None:
        self.seed()
        with self.Session() as session:
            session.execute(update(DmRuntime).where(DmRuntime.id == 1).values(**values))
            session.commit()

    def set_mode(self, mode: str) -> None:
        if mode not in {"observe", "demo_explore", "demo_filtered"}:
            raise ValueError("unknown mode")
        self.update_runtime(mode=mode)

    def try_lock(self, owner: str, now: datetime, ttl_seconds: int = 120) -> bool:
        self.seed()
        current = _naive(now)
        expires = current + timedelta(seconds=ttl_seconds)
        with self.Session() as session:
            result = session.execute(
                update(DmLock)
                .where(DmLock.id == 1)
                .where(or_(DmLock.owner.is_(None), DmLock.owner == owner, DmLock.expires_at < current))
                .values(owner=owner, expires_at=expires)
            )
            session.commit()
            return result.rowcount == 1

    def release_lock(self, owner: str) -> None:
        with self.Session() as session:
            session.execute(
                update(DmLock).where(DmLock.id == 1).where(DmLock.owner == owner).values(owner=None, expires_at=None)
            )
            session.commit()

    def audit(self, event: str, detail: str) -> None:
        with self.Session() as session:
            session.add(DmAudit(event=event, detail=detail[:4000]))
            session.commit()

    def add_tick(self, observation, *, previous_epoch=None, previous_precision=None, previous_quote=None, replay=False) -> str:
        quality = assess_tick(
            epoch=observation.broker_epoch,
            previous_epoch=previous_epoch,
            precision=observation.precision,
            previous_precision=previous_precision,
            expected_tick_seconds=settings.dm_expected_tick_seconds,
            quote_text=observation.quote_text,
            previous_quote_text=previous_quote,
            replay=replay,
        )
        flags = list(observation.flags) + list(quality.flags)
        if observation.broker_tick_id:
            dedup = f"id:{observation.symbol}:{observation.broker_tick_id}"
        else:
            with self.Session() as session:
                existing = session.query(DmTick).filter_by(
                    symbol=observation.symbol,
                    broker_epoch=observation.broker_epoch,
                    quote_text=observation.quote_text,
                ).count()
            dedup = f"seq:{observation.symbol}:{observation.broker_epoch}:{observation.quote_text}:{existing}"
            if replay:
                dedup = f"{dedup}:{int(observation.received_at.timestamp() * 1000)}"
        row = DmTick(
            symbol=observation.symbol,
            broker_epoch=observation.broker_epoch,
            received_at=_naive(observation.received_at),
            quote_wire=observation.quote_wire,
            quote_text=observation.quote_text,
            precision=observation.precision,
            last_digit=observation.last_digit,
            digit_value=observation.digit_value,
            source=observation.source,
            ingestion_id=observation.ingestion_id,
            broker_tick_id=observation.broker_tick_id,
            dedup_key=dedup,
            quality_flags=",".join(flags),
            pip_size_raw=observation.pip_size_raw,
        )
        with self.Session() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return "duplicate"
            marked = self._mark_conflicts(session, observation.symbol, observation.broker_epoch)
            if marked:
                session.commit()
            return "conflict" if marked or "conflict" in flags else "inserted"

    def _mark_conflicts(self, session, symbol: str, epoch: int) -> bool:
        rows = session.query(DmTick).filter_by(symbol=symbol, broker_epoch=epoch).all()
        quotes = {row.quote_text for row in rows}
        if len(quotes) < 2:
            return False
        for row in rows:
            flags = set(filter(None, (row.quality_flags or "").split(",")))
            flags.add("conflict")
            row.quality_flags = ",".join(sorted(flags))
        return True

    def latest_tick(self, symbol: str):
        with self.Session() as session:
            row = (
                session.query(DmTick)
                .filter(DmTick.symbol == symbol)
                .order_by(DmTick.broker_epoch.desc(), DmTick.id.desc())
                .first()
            )
            if row is not None:
                session.expunge(row)
            return row

    def series(self, symbol: str) -> list[DmTick]:
        with self.Session() as session:
            rows = (
                session.query(DmTick)
                .filter(DmTick.symbol == symbol)
                .order_by(DmTick.broker_epoch.asc(), DmTick.id.asc())
                .all()
            )
            session.expunge_all()
            return rows

    def risk_snapshot(self, now: datetime) -> RiskSnapshot:
        runtime = self.runtime()
        start, end = trading_day_bounds(now, runtime.reset_timezone)
        start_n, end_n = _naive(start), _naive(end)
        with self.Session() as session:
            copied = [
                {
                    "status": contract.status,
                    "sell_time": contract.sell_time,
                    "purchase_time": contract.purchase_time,
                    "profit": contract.profit,
                }
                for contract in session.query(DmContract).all()
            ]
        open_count = 0
        trades_today = 0
        pnl_today = 0.0
        last_settled = None
        for contract in copied:
            if contract["status"] == "open":
                open_count += 1
            sold = contract["sell_time"] or contract["purchase_time"]
            if contract["status"] != "open" and sold is not None and start_n <= sold < end_n:
                trades_today += 1
                if contract["profit"] is not None:
                    pnl_today += float(contract["profit"])
            if contract["sell_time"] is not None and (last_settled is None or contract["sell_time"] > last_settled):
                last_settled = contract["sell_time"]
        if last_settled is not None and last_settled.tzinfo is None:
            last_settled = last_settled.replace(tzinfo=timezone.utc)
        return RiskSnapshot(
            timezone_name=runtime.reset_timezone,
            stake=float(runtime.stake),
            cooldown_seconds=int(runtime.cooldown_seconds),
            max_trades_per_day=int(runtime.max_trades_per_day),
            daily_loss_limit=float(runtime.daily_loss_limit),
            daily_profit_stop=float(runtime.daily_profit_stop),
            max_open_contracts=1,
            open_contracts=open_count,
            trades_today=trades_today,
            pnl_today=pnl_today,
            last_settled_at=last_settled,
            emergency_stop=bool(runtime.emergency_stop),
            paused=bool(runtime.paused),
            uncertain=bool(runtime.uncertain_block),
            broker_min_stake=runtime.broker_min_stake,
        )

    def record_decision(self, **values) -> None:
        if "probabilities" in values:
            values["probabilities_json"] = json.dumps(values.pop("probabilities"))
        with self.Session() as session:
            session.add(DmDecision(**values))
            session.commit()

    def enqueue_history(self, symbol: str, target: int, ingestion_id: str) -> int:
        with self.Session() as session:
            job = DmIngestJob(
                ingestion_id=ingestion_id,
                symbol=symbol,
                target_ticks=target,
                status="queued",
            )
            session.add(job)
            session.commit()
            return job.id

    def enqueue_train(self, enable_mlp: bool) -> int:
        with self.Session() as session:
            job = DmTrainJob(status="queued", enable_mlp=enable_mlp, progress="queued")
            session.add(job)
            session.commit()
            return job.id

    def save_model_row(self, meta: dict) -> int:
        with self.Session() as session:
            row = DmModel(
                name=meta["selected_model"],
                schema=meta["schema"],
                seed=int(meta["seed"]),
                path=meta["path"],
                checksum=meta["checksum"],
                is_active=False,
                dataset_version=meta["dataset_version"],
                time_start=meta.get("time_start"),
                time_end=meta.get("time_end"),
                params_json=json.dumps({"margin": meta.get("margin"), "margin_source": meta.get("margin_source")}),
                metrics_json=json.dumps(
                    {
                        "final_test": meta.get("final_test"),
                        "comparison_to_uniform": meta.get("comparison_to_uniform"),
                        "selection_log_loss": meta.get("selection_log_loss"),
                        "walk_forward": meta.get("walk_forward"),
                        "edge_statement": meta.get("edge_statement"),
                        "has_demonstrated_edge": False,
                        "split_counts": meta.get("split_counts"),
                        "assumed_payout_simulation": meta.get("assumed_payout_simulation"),
                    }
                ),
                calibration=meta.get("calibration_method") or "temperature_scaling",
                dependency_versions=json.dumps(meta.get("dependency_versions") or {}),
            )
            session.add(row)
            session.commit()
            return row.id

    def promote(self, model_id: int) -> None:
        with self.Session() as session:
            session.execute(update(DmModel).values(is_active=False))
            row = session.get(DmModel, model_id)
            if row is None:
                raise ValueError("model not found")
            row.is_active = True
            session.commit()
        self.update_runtime(active_model_id=model_id)

    def active_model(self):
        with self.Session() as session:
            row = session.query(DmModel).filter_by(is_active=True).order_by(DmModel.id.desc()).first()
            if row is not None:
                session.expunge(row)
            return row

    def job_flags(self, model, job_id: int) -> dict | None:
        with self.Session() as session:
            row = session.get(model, job_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "status": row.status,
                "cancel_requested": bool(getattr(row, "cancel_requested", False)),
            }

    def next_job(self, model) -> dict | None:
        with self.Session() as session:
            row = session.query(model).filter_by(status="queued").order_by(model.id.asc()).first()
            if row is None:
                return None
            payload = {"id": row.id, "status": row.status}
            for key in (
                "ingestion_id",
                "symbol",
                "target_ticks",
                "ticks_stored",
                "checkpoint_end",
                "cancel_requested",
                "enable_mlp",
            ):
                if hasattr(row, key):
                    payload[key] = getattr(row, key)
            return payload

    def save_job(self, model, job_id: int, **values) -> None:
        with self.Session() as session:
            session.execute(update(model).where(model.id == job_id).values(**values))
            session.commit()

    def tick_count(self, symbol: str | None = None) -> int:
        with self.Session() as session:
            query = session.query(DmTick)
            if symbol:
                query = query.filter(DmTick.symbol == symbol)
            return query.count()

    def list_models(self) -> list[dict]:
        with self.Session() as session:
            rows = session.query(DmModel).order_by(DmModel.id.desc()).limit(20).all()
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "created_at": None if row.created_at is None else row.created_at.isoformat(),
                    "is_active": bool(row.is_active),
                    "checksum": row.checksum,
                    "dataset_version": row.dataset_version,
                    "schema": row.schema,
                    "calibration": row.calibration,
                    "has_demonstrated_edge": False,
                    "metrics": json.loads(row.metrics_json or "{}"),
                    "dependency_versions": json.loads(row.dependency_versions or "{}"),
                }
                for row in rows
            ]

    def list_trades(self, limit: int = 50) -> list[dict]:
        with self.Session() as session:
            rows = session.query(DmContract).order_by(DmContract.id.desc()).limit(limit).all()
            return [_contract_dict(row) for row in rows]

    def list_decisions(self, limit: int = 30) -> list[dict]:
        with self.Session() as session:
            rows = session.query(DmDecision).order_by(DmDecision.id.desc()).limit(limit).all()
            return [
                {
                    "id": row.id,
                    "created_at": None if row.created_at is None else row.created_at.isoformat(),
                    "mode": row.mode,
                    "action": row.action,
                    "reason": row.reason,
                    "digit": row.digit,
                    "break_even": row.break_even,
                    "expected_value": row.expected_value,
                    "ask_price": row.ask_price,
                    "total_payout": row.total_payout,
                    "probabilities": json.loads(row.probabilities_json) if row.probabilities_json else None,
                }
                for row in rows
            ]

    def list_audit(self, limit: int = 30) -> list[dict]:
        with self.Session() as session:
            rows = session.query(DmAudit).order_by(DmAudit.id.desc()).limit(limit).all()
            return [
                {
                    "id": row.id,
                    "created_at": None if row.created_at is None else row.created_at.isoformat(),
                    "event": row.event,
                    "detail": row.detail,
                }
                for row in rows
            ]

    def equity(self) -> list[dict]:
        with self.Session() as session:
            rows = (
                session.query(DmContract)
                .filter(DmContract.profit.is_not(None))
                .order_by(DmContract.id.asc())
                .all()
            )
            total = 0.0
            peak = 0.0
            points = []
            for row in rows:
                total += float(row.profit)
                peak = max(peak, total)
                points.append(
                    {
                        "contract_id": row.broker_contract_id,
                        "equity": total,
                        "drawdown": peak - total,
                        "profit": float(row.profit),
                    }
                )
            return points

    def export_rows(self, symbol: str) -> list[DmTick]:
        return self.series(symbol)

    def record_dataset(self, **values) -> None:
        with self.Session() as session:
            session.add(DmDataset(**values))
            session.commit()

    def saved_touch_count(self, symbol: str = "R_100") -> int:
        from sqlalchemy.exc import SQLAlchemyError

        from app.models.tick import Tick

        try:
            with self.Session() as session:
                return int(session.query(Tick).filter(Tick.symbol == symbol).count())
        except SQLAlchemyError:
            return 0

    def open_touch_copy_job(self) -> int:
        import uuid

        with self.Session() as session:
            job = DmIngestJob(
                ingestion_id=uuid.uuid4().hex,
                symbol="R_100",
                target_ticks=self.saved_touch_count("R_100"),
                status="copying",
                note="Copying saved touch-bot ticks. This page keeps updating the count.",
            )
            session.add(job)
            session.commit()
            return int(job.id)

    def import_saved_ticks(self, symbol: str = "R_100", progress_job_id: int | None = None) -> dict:
        """Copy the touch bot's saved R_100 ticks. Digits are rebuilt from the numeric quote and pip size."""
        import uuid
        from dataclasses import replace

        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.exc import SQLAlchemyError

        from app.digitmatch.digits import DigitError, decimal_places
        from app.digitmatch.ingestion import normalize_observation
        from app.models.tick import Tick

        symbol = str(symbol or "").strip()
        if symbol != "R_100":
            raise ValueError("Only saved Volatility 100 Index ticks (R_100) can be copied. The 1-second index is not used.")

        try:
            with self.Session() as session:
                raw = (
                    session.query(Tick.epoch, Tick.quote, Tick.pip_size, Tick.is_gap, Tick.received_at, Tick.id)
                    .filter(Tick.symbol == symbol)
                    .order_by(Tick.epoch.asc(), Tick.id.asc())
                    .all()
                )
        except SQLAlchemyError as exc:
            raise RuntimeError("Saved touch-bot ticks could not be read") from exc

        ingestion_id = uuid.uuid4().hex
        previous_epoch = None
        previous_precision = None
        previous_quote = None
        batch: list[dict] = []
        copied = 0
        unreadable = 0
        inserter = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert

        def flush(session) -> None:
            nonlocal batch, copied
            if not batch:
                return
            copied += len(batch)
            stmt = inserter(DmTick).values(batch)
            if self.engine.dialect.name == "postgresql":
                stmt = stmt.on_conflict_do_nothing(constraint="uq_dm_ticks_dedup")
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=["dedup_key"])
            session.execute(stmt)
            session.commit()
            batch = []
            if progress_job_id is not None:
                self.save_job(
                    DmIngestJob,
                    progress_job_id,
                    ticks_stored=copied,
                    target_ticks=len(raw),
                    status="copying",
                )

        with self.Session() as session:
            before = session.query(DmTick).filter(DmTick.symbol == symbol, DmTick.source == "touch_bot").count()
            for epoch, quote, pip_size, is_gap, received_at, _row_id in raw:
                try:
                    if pip_size is None:
                        observation = normalize_observation(
                            symbol=symbol,
                            quote=quote,
                            epoch=int(epoch),
                            pip_size=None,
                            source="touch_bot",
                            ingestion_id=ingestion_id,
                            broker_tick_id=None,
                            received_at=received_at,
                        )
                    else:
                        precision = decimal_places(pip_size)
                        text = f"{float(quote):.{precision}f}"
                        observation = normalize_observation(
                            symbol=symbol,
                            quote=text,
                            epoch=int(epoch),
                            pip_size=precision,
                            source="touch_bot",
                            ingestion_id=ingestion_id,
                            broker_tick_id=None,
                            received_at=received_at,
                        )
                except (DigitError, ValueError, TypeError):
                    unreadable += 1
                    continue
                observation = replace(observation, flags=observation.flags + ("numeric_quote_reconstructed",))
                quality = assess_tick(
                    epoch=observation.broker_epoch,
                    previous_epoch=previous_epoch,
                    precision=observation.precision,
                    previous_precision=previous_precision,
                    expected_tick_seconds=settings.dm_expected_tick_seconds,
                    quote_text=observation.quote_text,
                    previous_quote_text=previous_quote,
                )
                flags = list(observation.flags)
                if is_gap:
                    flags.append("gap")
                flags.extend(quality.flags)
                batch.append(
                    {
                        "symbol": symbol,
                        "broker_epoch": observation.broker_epoch,
                        "received_at": _naive(observation.received_at),
                        "quote_wire": observation.quote_wire,
                        "quote_text": observation.quote_text,
                        "precision": observation.precision,
                        "last_digit": observation.last_digit,
                        "digit_value": observation.digit_value,
                        "source": "touch_bot",
                        "ingestion_id": ingestion_id,
                        "broker_tick_id": None,
                        "dedup_key": f"touch:{symbol}:{observation.broker_epoch}",
                        "quality_flags": ",".join(dict.fromkeys(flags)),
                        "pip_size_raw": observation.pip_size_raw,
                    }
                )
                previous_epoch = observation.broker_epoch
                previous_precision = observation.precision
                previous_quote = observation.quote_text
                if len(batch) >= 2000:
                    flush(session)
            flush(session)
            after = session.query(DmTick).filter(DmTick.symbol == symbol, DmTick.source == "touch_bot").count()

        inserted = max(0, int(after) - int(before))
        available = len(raw)
        note = (
            f"Copied {inserted} new ticks from {available} saved {symbol} rows. "
            "Quotes were stored as numbers, so each last digit was rebuilt with that tick's pip size. "
            "Volatility 100 (1s) was not copied."
        )
        self.record_dataset(name="touch-bot ticks", source="touch_bot", sha256=None, row_count=inserted, note=note)
        self.audit("touch_ticks_import", note)
        return {
            "ok": True,
            "symbol": symbol,
            "available": available,
            "inserted": inserted,
            "already_copied": available - inserted - unreadable,
            "unreadable": unreadable,
            "note": note,
        }
