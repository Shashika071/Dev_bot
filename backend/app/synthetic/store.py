"""Durable, transactional daily quotas and at-most-once demo order attempts.

SQLite lives on the existing persistent model volume. BEGIN IMMEDIATE serializes
reservation across API processes; a network request never holds a DB transaction.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.synthetic.policy import default_config, local_day, validate_config


class SyntheticStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS synthetic_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS synthetic_signals (
                    id TEXT PRIMARY KEY, day TEXT NOT NULL, created_epoch INTEGER NOT NULL,
                    expiry_epoch INTEGER NOT NULL, setup_key TEXT NOT NULL UNIQUE,
                    execution_status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS synthetic_lease (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, expires INTEGER);
                CREATE TABLE IF NOT EXISTS evidence_ticks (epoch INTEGER PRIMARY KEY, quote REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO synthetic_state VALUES(1, ?)",
                       (json.dumps({"running": False, "config": default_config()}),))
            db.execute("INSERT OR IGNORE INTO synthetic_lease VALUES(1, '', 0)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self) -> dict:
        with self.connection() as db:
            return json.loads(db.execute("SELECT payload FROM synthetic_state WHERE id=1").fetchone()[0])

    def update_state(self, **updates) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT payload FROM synthetic_state WHERE id=1").fetchone()[0])
            if "config" in updates:
                updates["config"] = validate_config(updates["config"], state.get("config"))
            state.update(updates)
            db.execute("UPDATE synthetic_state SET payload=? WHERE id=1", (json.dumps(state, allow_nan=False),))
        return state

    def signals(self, limit=30) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM synthetic_signals ORDER BY created_epoch DESC LIMIT ?", (limit,))]

    def daily_count(self, now: int) -> int:
        with self.connection() as db:
            return db.execute("SELECT count(*) FROM synthetic_signals WHERE day=?", (local_day(now),)).fetchone()[0]

    def acquire(self, now: int, ttl=240) -> str | None:
        owner = uuid.uuid4().hex
        with self.connection() as db:
            changed = db.execute("UPDATE synthetic_lease SET owner=?, expires=? WHERE id=1 AND expires<=?",
                                 (owner, now + ttl, now)).rowcount
        return owner if changed else None

    def release(self, owner: str):
        with self.connection() as db:
            db.execute("UPDATE synthetic_lease SET expires=0 WHERE id=1 AND owner=?", (owner,))

    def reserve(self, signal: dict, now: int, *, expected_config: dict | None = None,
                stake_budget: float | None = None) -> tuple[dict | None, str | None]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT payload FROM synthetic_state WHERE id=1").fetchone()[0])
            config = state["config"]
            if expected_config is not None and expected_config != config:
                return None, "Configuration changed during analysis; a new scan is required"
            if not state.get("running"):
                return None, "Scanner stopped"
            count = db.execute("SELECT count(*) FROM synthetic_signals WHERE day=?", (local_day(now),)).fetchone()[0]
            if count >= config["max_signals_per_day"]:
                return None, "Daily signal cap reached (Asia/Colombo)"
            if config["demo_auto_trade"] and stake_budget is not None:
                if not math.isfinite(stake_budget) or stake_budget <= 0:
                    return None, "Invalid daily demo stake budget"
                rows = db.execute("SELECT payload FROM synthetic_signals WHERE day=? AND execution_status NOT IN ('signal_only','blocked','failed')",
                                  (local_day(now),)).fetchall()
                committed = sum(float(json.loads(row[0]).get("stake", config["stake"])) for row in rows)
                if committed + config["stake"] > stake_budget + 1e-9:
                    return None, "Daily demo stake budget reached (conservative maximum loss)"
            if db.execute("SELECT 1 FROM synthetic_signals WHERE execution_status IN ('submitting','unknown') LIMIT 1").fetchone():
                return None, "An earlier purchase is unresolved; reconcile it with Deriv before further execution"
            latest = db.execute("SELECT created_epoch,expiry_epoch FROM synthetic_signals ORDER BY created_epoch DESC LIMIT 1").fetchone()
            if latest and (now - latest["created_epoch"] < config["cooldown_seconds"] or latest["expiry_epoch"] > now):
                return None, "Waiting for the previous holding period and cooldown to finish"
            if db.execute("SELECT 1 FROM synthetic_signals WHERE setup_key=?", (signal["setup_key"],)).fetchone():
                return None, "This closed-candle setup was already issued"
            row = {**signal, "id": uuid.uuid4().hex,
                   "execution_status": "submitting" if config["demo_auto_trade"] else "signal_only"}
            db.execute("INSERT INTO synthetic_signals VALUES(?,?,?,?,?,?,?)",
                       (row["id"], local_day(now), now, row["expiry_epoch"], row["setup_key"], row["execution_status"], json.dumps(row, allow_nan=False)))
        return row, None

    def finish(self, signal_id: str, result: dict):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM synthetic_signals WHERE id=?", (signal_id,)).fetchone()
            if not row:
                raise ValueError("Signal does not exist")
            signal = json.loads(row[0])
            signal.update(execution_status=result.get("status", "bought" if result.get("ok") else "blocked"), execution_result=result)
            for key in ("estimated_expiry_epoch", "date_expiry", "expiry_epoch"):
                value = result.get(key)
                if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                    signal["expiry_epoch"] = max(signal["expiry_epoch"], int(value))
            db.execute("UPDATE synthetic_signals SET execution_status=?,expiry_epoch=?,payload=? WHERE id=?",
                       (signal["execution_status"], signal["expiry_epoch"], json.dumps(signal, allow_nan=False), signal_id))

    def observe_ticks(self, ticks: list[dict], now: int) -> None:
        """Persist before resolving; restart/gaps cannot silently become winning trades."""
        from app.synthetic.evidence import clean_ticks, tick_outcome
        ticks = clean_ticks(ticks)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany("INSERT OR IGNORE INTO evidence_ticks VALUES (?,?)",
                           [(row["epoch"], row["quote"]) for row in ticks])
            records = db.execute("SELECT id,payload FROM synthetic_signals WHERE created_epoch>=?", (now - 86400,)).fetchall()
            for record in records:
                signal = json.loads(record["payload"])
                paper = signal.get("paper")
                if not paper or paper.get("outcome") not in {"pending", "unresolved"}:
                    continue
                requested = signal.get("observed_at", signal["created_epoch"])
                duration = signal["duration_seconds"]
                if now < requested + duration + 12:
                    continue
                path = [dict(row) for row in db.execute(
                    "SELECT epoch,quote FROM evidence_ticks WHERE epoch>=? AND epoch<=? ORDER BY epoch",
                    (requested, requested + duration + 12))]
                result = tick_outcome(path, requested_entry=requested, duration=duration,
                                      direction=signal["direction"], net_payout=signal.get("net_payout"))
                signal["paper"] = result
                db.execute("UPDATE synthetic_signals SET payload=? WHERE id=?",
                           (json.dumps(signal, allow_nan=False), record["id"]))
            db.execute("DELETE FROM evidence_ticks WHERE epoch<?", (now - 86400,))

    def performance(self) -> dict:
        from app.synthetic.evidence import summarize
        with self.connection() as db:
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM synthetic_signals ORDER BY created_epoch")]
        groups = {}
        for signal in rows:
            if not signal.get("paper"):
                continue
            key = (signal.get("strategy_version", "legacy"), signal["duration_seconds"])
            groups.setdefault(key, []).append(signal["paper"])
        return {
            "source": "forward_tick_paper", "broker_settlement": False,
            "note": "First observed tick at entry and expiry. Paper estimates; broker settlements can differ. Missing quotes leave profit unknown.",
            "groups": [{"strategy": key[0], "duration_seconds": key[1], **summarize(items)} for key, items in groups.items()],
        }
