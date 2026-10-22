"""Connect the research engine to stored R_100 candles. This module places no orders."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import structlog

from app.trading_ml.config import LIMITATIONS, MIN_EVALUATION_BARS, validate_duration, validate_payout
from app.trading_ml.data.collector import validate_hours as validate_hours_safe
from app.trading_ml.features.causal import align_entry
from app.trading_ml.models.sequence import sequence_status
from app.trading_ml.monitoring.approval import promotion_allowed
from app.trading_ml.paper_trading.journal import read_journal, resolve_outcomes, summarize, write_journal
from app.trading_ml.plain import to_plain
from app.trading_ml.risk.limits import colombo_day, load_limits, next_stake
from app.trading_ml.signals.engine import apply_live_gates

logger = structlog.get_logger(__name__)

_job = {"status": "idle", "progress": "", "error": None, "kind": None}
_task: asyncio.Task | None = None
_lock: asyncio.Lock | None = None


def research_root(model_dir: str | Path) -> Path:
    return Path(model_dir) / "synthetic_r100" / "trading_ml"


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Research file must contain an object")
    return payload


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(to_plain(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _candles(model_dir: str | Path) -> list[dict]:
    from app.synthetic.history import history_root, load_candles
    return load_candles(history_root(model_dir), "1m")


def research_status(model_dir: str | Path, *, now: int | None = None,
                    quote_epoch: int | None = None) -> dict:
    try:
        return _status(model_dir, now=now, quote_epoch=quote_epoch)
    except Exception as exc:
        logger.warning("research_status_failed", error=f"{type(exc).__name__}: {exc}")
        card = _empty(f"Research status could not be read ({type(exc).__name__}). The one-minute bot is unchanged.")
        card["job"] = dict(_job)
        return card


def _empty(reason: str) -> dict:
    now = int(time.time())
    return {
        "market": "R_100", "contract": "Rise/Fall", "duration_seconds": 300,
        "direction": "NO TRADE", "final_signal": "NO TRADE", "candidate_direction": None,
        "paper_direction": None, "calibrated_win_probability": None, "market_regime": "unknown",
        "volatility": "unavailable", "expected_value": None, "model_status": "RESEARCH",
        "approval_state": "RESEARCH", "approval_reasons": [], "reason": reason,
        "places_trades": False, "stale": False, "models": [], "backtest": None,
        "paper": summarize([], now=now), "data_quality": None, "dataset": None,
        "limitations": list(LIMITATIONS), "optional_research": sequence_status(),
        "selected_model": None, "notes": [], "ticks": {"ticks": 0, "status": "not_downloaded"},
        "job": dict(_job), "payout": None,
    }


def _status(model_dir: str | Path, *, now: int | None, quote_epoch: int | None) -> dict:
    stamp = int(time.time()) if now is None else int(now)
    root = research_root(model_dir)
    report = _read_json(root / "latest_report.json")
    ticks = _read_json(root / "ticks_status.json") or {"ticks": 0, "status": "not_downloaded"}
    paper = summarize(read_journal(root / "paper_journal.jsonl"), now=stamp)
    if report is None:
        card = _empty("No research evaluation has been run. The one-minute FRAMA bot is unchanged.")
        card["paper"] = paper
        card["ticks"] = ticks
        card["job"] = dict(_job)
        return card
    signal = apply_live_gates(
        dict(report.get("signal") or {}), now=stamp, quote_epoch=quote_epoch,
        decision_epoch=(report.get("signal") or {}).get("decision_epoch"),
    )
    return {
        **signal,
        "approval_state": report.get("approval_state") or "RESEARCH",
        "approval_reasons": (report.get("approval") or {}).get("reasons") or [],
        "models": report.get("models") or [],
        "backtest": report.get("backtest"),
        "paper": paper,
        "data_quality": report.get("data_quality"),
        "dataset": report.get("dataset"),
        "limitations": report.get("limitations") or list(LIMITATIONS),
        "optional_research": report.get("optional_research") or {"sequence": sequence_status()},
        "selected_model": report.get("selected_model"),
        "notes": report.get("notes") or [],
        "ticks": ticks,
        "job": dict(_job),
        "trained_at": report.get("trained_at"),
        "test": report.get("test"),
        "places_trades": False,
    }


def _guard_other_jobs() -> None:
    from app.synthetic.jobs import synthetic_jobs
    snapshot = synthetic_jobs.snapshot()
    history_state = ((snapshot.get("history") or {}).get("job") or {}).get("status")
    if history_state == "running" or snapshot.get("train", {}).get("status") == "running":
        raise ValueError("Wait for the current history download or one-minute model training to finish.")


def _persist(model_dir: str | Path, report: dict, bundle: dict) -> dict:
    import joblib

    root = research_root(model_dir)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    candidate = root / "candidates" / stamp
    candidate.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, candidate / "model.joblib")
    previous = _read_json(root / "active.json")
    allowed, reason = promotion_allowed(previous, report)
    report = {**report, "promotion": {"allowed": allowed, "reason": reason}}
    _write_json(candidate / "report.json", report)
    _write_json(root / "latest_report.json", report)
    if allowed:
        _write_json(root / "active.json", {
            "state": "SIGNAL_READY",
            "test_log_loss": report.get("test", {}).get("log_loss"),
            "trained_at": report.get("trained_at"),
            "path": str(candidate / "model.joblib"),
        })
    return report


async def start_research(model_dir: str | Path, *, duration_seconds: int = 300,
                         payout: float | None = None, models: list[str] | None = None) -> dict:
    global _task
    duration_seconds = validate_duration(duration_seconds)
    payout = validate_payout(payout)
    async with _guard_lock():
        if _task is not None and not _task.done():
            raise ValueError("A research job is already running")
        _guard_other_jobs()
        candles = await asyncio.to_thread(_candles, model_dir)
        if len(candles) < MIN_EVALUATION_BARS:
            raise ValueError(
                "Download at least 400 closed R_100 one-minute candles before research evaluation. "
                "Missing history was not invented."
            )
        _job.update(status="running", kind="evaluate", progress="Starting research evaluation",
                    error=None, started_at=int(time.time()), finished_at=None)

        async def run() -> None:
            try:
                from app.trading_ml.training.pipeline import run_evaluation
                limits, extra = load_limits(research_root(model_dir) / "risk.json")
                paper = summarize(read_journal(research_root(model_dir) / "paper_journal.jsonl"), now=int(time.time()))
                report, bundle = await asyncio.to_thread(
                    run_evaluation, candles, duration_seconds=duration_seconds, payout=payout,
                    limits=limits, models=models, paper=paper, extra_risk=extra, progress=_job,
                )
                await asyncio.to_thread(_persist, model_dir, report, bundle)
                _job.update(status="done", progress="Research evaluation complete", error=None,
                            finished_at=int(time.time()))
            except Exception as exc:
                logger.warning("research_evaluation_failed", error=f"{type(exc).__name__}: {exc}")
                _job.update(status="error", progress="Research evaluation failed",
                            error=f"{type(exc).__name__}: {exc}", finished_at=int(time.time()))

        _task = asyncio.create_task(run())
        return research_status(model_dir)


async def start_score(model_dir: str | Path, *, payout: float | None = None) -> dict:
    global _task
    payout = validate_payout(payout)
    async with _guard_lock():
        if _task is not None and not _task.done():
            raise ValueError("A research job is already running")
        root = research_root(model_dir)
        if not (root / "latest_report.json").exists() or not (root / "candidates").exists():
            raise ValueError("Run a research evaluation before scoring a new candle.")
        _job.update(status="running", kind="score", progress="Scoring the latest closed candle",
                    error=None, started_at=int(time.time()), finished_at=None)

        async def run() -> None:
            try:
                await asyncio.to_thread(_score, model_dir, payout)
                _job.update(status="done", progress="Latest candle scored", error=None,
                            finished_at=int(time.time()))
            except Exception as exc:
                _job.update(status="error", progress="Scoring failed",
                            error=f"{type(exc).__name__}: {exc}", finished_at=int(time.time()))

        _task = asyncio.create_task(run())
        return research_status(model_dir)


def _score(model_dir: str | Path, payout: float | None) -> None:
    import joblib

    from app.trading_ml.training.pipeline import score_bundle
    root = research_root(model_dir)
    report = _read_json(root / "latest_report.json")
    if report is None:
        raise ValueError("No research report is stored.")
    candidates = sorted((root / "candidates").glob("*/model.joblib"))
    if not candidates:
        raise ValueError("No saved research model is available.")
    bundle = joblib.load(candidates[-1])
    limits, extra = load_limits(root / "risk.json")
    candles = _candles(model_dir)
    paper = summarize(read_journal(root / "paper_journal.jsonl"), now=int(time.time()))
    card = score_bundle(
        bundle, candles, report=report, payout=payout, limits=limits, paper=paper, extra_risk=extra,
    )
    report["signal"] = card
    _write_json(root / "latest_report.json", report)


async def start_ticks(model_dir: str | Path, *, hours: int = 6) -> dict:
    global _task
    hours = validate_hours_safe(hours)
    async with _guard_lock():
        if _task is not None and not _task.done():
            raise ValueError("A research job is already running")
        _job.update(status="running", kind="ticks", progress="Connecting to Deriv public ticks",
                    error=None, started_at=int(time.time()), finished_at=None)

        async def run() -> None:
            from app.deriv.client import DerivWSClient
            from app.trading_ml.data.collector import download_ticks
            client = DerivWSClient()
            try:
                await client.connect()
                summary = await download_ticks(
                    research_root(model_dir) / "ticks.parquet", hours=hours, client=client, progress=_job,
                )
                _write_json(research_root(model_dir) / "ticks_status.json", {**summary, "status": "saved"})
                _job.update(status="done", progress="Tick download complete", error=None,
                            finished_at=int(time.time()))
            except Exception as exc:
                _job.update(status="error", progress="Tick download failed",
                            error=f"{type(exc).__name__}: {exc}", finished_at=int(time.time()))
            finally:
                await client.disconnect()

        _task = asyncio.create_task(run())
        return research_status(model_dir)


def record_paper(model_dir: str | Path, *, now: int | None = None, quote_epoch: int | None = None) -> dict:
    stamp = int(time.time()) if now is None else int(now)
    root = research_root(model_dir)
    report = _read_json(root / "latest_report.json")
    if report is None:
        raise ValueError("Run a research evaluation before recording a paper observation.")
    card = research_status(model_dir, now=stamp, quote_epoch=quote_epoch)
    candles = _candles(model_dir)
    rows = resolve_outcomes(read_journal(root / "paper_journal.jsonl"), candles, now=stamp)
    direction = card.get("final_signal") if card.get("final_signal") in {"RISE", "FALL"} else card.get("paper_direction")
    if direction in {"RISE", "FALL"}:
        decision = int(card["decision_epoch"])
        duration = int(card["duration_seconds"])
        entry_epoch = align_entry(decision, int(report.get("entry_delay_seconds") or 0), 60)
        event = {
            "kind": "trade", "source": "paper", "mode": card.get("approval_state"),
            "timestamp": stamp, "day": colombo_day(stamp),
            "direction": direction, "decision_epoch": decision,
            "entry_epoch": entry_epoch, "expiry_epoch": entry_epoch + duration,
            "duration_seconds": duration, "payout": card.get("payout"),
            "stake": next_stake(), "outcome": "open", "profit_per_stake": None,
            "reason": card.get("reason"),
        }
    else:
        event = {
            "kind": "rejection", "source": "paper", "timestamp": stamp,
            "direction": "NO TRADE", "reason": card.get("reason"), "stake": next_stake(),
        }
    rows.append(event)
    write_journal(root / "paper_journal.jsonl", rows)
    return research_status(model_dir, now=stamp, quote_epoch=quote_epoch)


def _guard_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock
