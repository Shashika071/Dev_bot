"""Dashboard and control API for demo Digit Matches research."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.digitmatch import (
    API_FAMILY,
    CONTRACT_TYPE,
    DURATION_TICKS,
    EXPECTED_DISPLAY_NAME,
    EXPECTED_LEGACY_SYMBOL,
    FRESHNESS_POLICY,
    TARGET_NOTE,
)
from app.digitmatch.credentials import resolve_digitmatch_credentials
from app.digitmatch.ingestion import normalize_observation
from app.digitmatch.state import resolve_ui_state
from app.digitmatch.store import SqlStore
from app.models.digitmatch import DmIngestJob, DmTrainJob

router = APIRouter(prefix="/api/digitmatch", tags=["digitmatch"])
_copy_guard = threading.Lock()
_copy_running = False


class ModeBody(BaseModel):
    mode: str = Field(pattern="^(observe|demo_explore|demo_filtered)$")


class RiskBody(BaseModel):
    stake: float = Field(gt=0, le=1000)
    cooldown_seconds: int = Field(ge=0, le=86400)
    max_trades_per_day: int = Field(ge=0, le=500)
    daily_loss_limit: float = Field(ge=0, le=100000)
    daily_profit_stop: float = Field(ge=0, le=100000)
    reset_timezone: str = Field(min_length=1, max_length=64)
    margin: float = Field(ge=0, le=0.5)


class LoginBody(BaseModel):
    password: str


class HistoryBody(BaseModel):
    target_ticks: int = Field(default=100000, ge=1, le=2_000_000)


class TrainBody(BaseModel):
    enable_mlp: bool = False


def _store() -> SqlStore:
    store = SqlStore()
    store.create_schema()
    store.seed()
    return store


def _require_login(request: Request) -> None:
    header = request.headers.get("authorization") or ""
    token = header.removeprefix("Bearer ").strip()
    if not token or not _valid_session(token):
        raise HTTPException(status_code=401, detail="authentication required")


def _read_access(request: Request) -> None:
    if settings.dm_require_app_auth:
        _require_login(request)


def _control_access(request: Request) -> None:
    if settings.dm_require_app_auth:
        _require_login(request)
        return
    if not settings.dm_allow_demo_control:
        raise HTTPException(
            status_code=403,
            detail="Digit Matches controls are locked. Set DM_ALLOW_DEMO_CONTROL=true only on a localhost install, or set DM_REQUIRE_APP_AUTH=true.",
        )


def _valid_session(token: str) -> bool:
    try:
        exp_text, signature = token.split(".", 1)
        exp = int(exp_text)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(
        settings.dm_session_secret.encode(),
        exp_text.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _issue_session() -> str:
    exp = int(time.time()) + 12 * 3600
    signature = hmac.new(
        settings.dm_session_secret.encode(),
        str(exp).encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{exp}.{signature}"


@router.post("/login")
def login(body: LoginBody):
    if not settings.dm_require_app_auth:
        return {"ok": True, "required": False}
    password = settings.dm_app_password or ""
    if not password or not hmac.compare_digest(body.password, password):
        raise HTTPException(status_code=401, detail="authentication required")
    return {"ok": True, "token": _issue_session(), "required": True}


@router.get("/health")
def health():
    return {"status": "ok", "service": "digitmatch", "api_family": API_FAMILY}


@router.get("/dashboard")
def dashboard(_: None = Depends(_read_access)):
    store = _store()
    runtime = store.runtime()
    now = datetime.now(timezone.utc)
    received = runtime.last_tick_received_at
    stale = True
    age = None
    if received is not None:
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        age = (now - received).total_seconds()
        stale = age > settings.dm_max_tick_age_seconds
    symbol = runtime.resolved_symbol or EXPECTED_LEGACY_SYMBOL
    decisions = store.list_decisions(1)
    last = decisions[0] if decisions else None
    trades = store.list_trades(100)
    open_trade = next((row for row in trades if row["status"] == "open"), None)
    risk = store.risk_snapshot(now)
    active = store.active_model()
    tick_count = store.tick_count(symbol if runtime.resolved_symbol else None)
    creds = resolve_digitmatch_credentials()
    snapshot = {
        "uncertain_block": bool(runtime.uncertain_block),
        "emergency_stop": bool(runtime.emergency_stop),
        "auth_status": runtime.auth_status,
        "credentials_configured": creds["configured"],
        "connection_status": runtime.connection_status,
        "demo_verified": bool(runtime.demo_verified),
        "instrument_error": runtime.instrument_error,
        "contract_error": runtime.contract_error,
        "stale": stale if runtime.connection_status == "online" else False,
        "features_ready": tick_count >= 1000,
        "paused": bool(runtime.paused),
        "risk_block": None if risk is None else None,
        "open_contract": open_trade is not None,
        "active_model": active is not None,
        "mode": runtime.mode,
        "last_skip_reason": None if last is None else last.get("reason"),
    }
    gate_reason = None
    if risk.trades_today >= risk.max_trades_per_day:
        gate_reason = "daily_trade_limit"
    elif risk.pnl_today <= -risk.daily_loss_limit:
        gate_reason = "daily_loss_limit"
    elif risk.pnl_today >= risk.daily_profit_stop:
        gate_reason = "daily_profit_stop"
    snapshot["risk_block"] = gate_reason
    history = store.next_job(DmIngestJob)
    train = store.next_job(DmTrainJob)
    with store.Session() as session:
        latest_history = session.query(DmIngestJob).order_by(DmIngestJob.id.desc()).first()
        latest_train = session.query(DmTrainJob).order_by(DmTrainJob.id.desc()).first()
        history_view = None if latest_history is None else {
            "id": latest_history.id,
            "status": latest_history.status,
            "ticks_stored": latest_history.ticks_stored,
            "target_ticks": latest_history.target_ticks,
            "note": latest_history.note,
            "error": latest_history.error,
        }
        train_view = None if latest_train is None else {
            "id": latest_train.id,
            "status": latest_train.status,
            "progress": latest_train.progress,
            "error": latest_train.error,
            "model_id": latest_train.model_id,
        }
    probabilities = None if last is None else last.get("probabilities")
    return {
        "demo_only": True,
        "api_family": API_FAMILY,
        "api_note": (
            "Digit Matches uses the classic WebSocket v3 API and a classic API token. "
            "PAT and OAuth are not accepted here."
        ),
        "target_note": TARGET_NOTE,
        "freshness_policy": FRESHNESS_POLICY,
        "credentials_configured": creds["configured"],
        "credential_source": creds["source"],
        "credential_kind": creds["kind"],
        "ui_state": resolve_ui_state(snapshot),
        "connection_status": runtime.connection_status,
        "auth_status": runtime.auth_status,
        "demo_verified": bool(runtime.demo_verified),
        "loginid": runtime.loginid,
        "balance": runtime.balance,
        "currency": runtime.currency,
        "instrument": {
            "expected_legacy_symbol": EXPECTED_LEGACY_SYMBOL,
            "expected_display_name": EXPECTED_DISPLAY_NAME,
            "resolved_symbol": runtime.resolved_symbol,
            "resolved_display": runtime.resolved_display,
            "matches_legacy_symbol": runtime.matches_legacy_symbol,
        },
        "contract": {
            "type": CONTRACT_TYPE,
            "duration_ticks": DURATION_TICKS,
            "ready": bool(runtime.contract_ready),
            "error": runtime.contract_error,
        },
        "freshness": {"age_seconds": age, "stale": snapshot["stale"], "last_epoch": runtime.last_tick_epoch},
        "mode": runtime.mode,
        "paused": bool(runtime.paused),
        "emergency_stop": bool(runtime.emergency_stop),
        "stop_note": "Stopping the bot blocks new orders. It does not cancel a contract that is already purchased.",
        "model": None
        if active is None
        else {
            "id": active.id,
            "name": active.name,
            "checksum": active.checksum,
            "created_at": None if active.created_at is None else active.created_at.isoformat(),
            "has_demonstrated_edge": False,
        },
        "probabilities": probabilities,
        "probability_label": "estimated probability",
        "decision": last,
        "active_contract": open_trade,
        "trades": trades,
        "equity": store.equity(),
        "pnl": {"daily": risk.pnl_today, "cumulative": sum(point["profit"] for point in store.equity())},
        "risk": {
            "stake": risk.stake,
            "cooldown_seconds": risk.cooldown_seconds,
            "max_trades_per_day": risk.max_trades_per_day,
            "trades_today": risk.trades_today,
            "daily_loss_limit": risk.daily_loss_limit,
            "daily_profit_stop": risk.daily_profit_stop,
            "pnl_today": risk.pnl_today,
            "reset_timezone": risk.timezone_name,
            "margin": runtime.margin,
            "broker_min_stake": risk.broker_min_stake,
            "max_open_contracts": 1,
        },
        "ticks_stored": tick_count,
        "touch_ticks_available": store.saved_touch_count("R_100"),
        "history_job": history_view,
        "train_job": train_view,
        "queued_history": history,
        "queued_train": train,
        "reconciliation": {
            "blocked": bool(runtime.uncertain_block),
            "reason": runtime.uncertain_reason,
        },
        "last_error": runtime.last_error,
        "audit": store.list_audit(20),
        "evaluation": None if active is None else __import__("json").loads(active.metrics_json or "{}"),
    }


@router.post("/mode")
def set_mode(body: ModeBody, _: None = Depends(_control_access)):
    store = _store()
    store.set_mode(body.mode)
    store.audit("mode", body.mode)
    return {"ok": True, "mode": body.mode}


@router.post("/pause")
def pause(_: None = Depends(_control_access)):
    store = _store()
    store.update_runtime(paused=True, pause_reason="operator pause")
    store.audit("pause", "new orders paused")
    return {"ok": True, "paused": True}


@router.post("/resume")
def resume(_: None = Depends(_control_access)):
    store = _store()
    store.update_runtime(paused=False, pause_reason=None)
    store.audit("resume", "pause cleared; emergency stop is unchanged")
    return {"ok": True, "paused": False}


@router.post("/emergency-stop")
def emergency_stop(_: None = Depends(_control_access)):
    store = _store()
    store.update_runtime(emergency_stop=True, paused=True, pause_reason="emergency stop")
    store.audit("emergency_stop", "new orders blocked; open contracts are not cancelled")
    return {"ok": True, "emergency_stop": True}


@router.post("/emergency-stop/clear")
def clear_emergency(_: None = Depends(_control_access)):
    store = _store()
    store.update_runtime(emergency_stop=False)
    store.audit("emergency_stop_clear", "emergency stop cleared")
    return {"ok": True, "emergency_stop": False}


@router.post("/risk")
def update_risk(body: RiskBody, _: None = Depends(_control_access)):
    store = _store()
    store.update_runtime(
        stake=body.stake,
        cooldown_seconds=body.cooldown_seconds,
        max_trades_per_day=body.max_trades_per_day,
        daily_loss_limit=body.daily_loss_limit,
        daily_profit_stop=body.daily_profit_stop,
        reset_timezone=body.reset_timezone,
        margin=body.margin,
    )
    store.audit("risk", "limits updated")
    return {"ok": True}


@router.post("/history/start")
def start_history(body: HistoryBody, _: None = Depends(_control_access)):
    store = _store()
    runtime = store.runtime()
    symbol = runtime.resolved_symbol or EXPECTED_LEGACY_SYMBOL
    ingestion_id = uuid.uuid4().hex
    job_id = store.enqueue_history(symbol, body.target_ticks, ingestion_id)
    store.audit("history_start", ingestion_id)
    return {"ok": True, "job_id": job_id, "ingestion_id": ingestion_id, "symbol": symbol}


@router.post("/history/cancel")
def cancel_history(_: None = Depends(_control_access)):
    store = _store()
    with store.Session() as session:
        row = (
            session.query(DmIngestJob)
            .filter(DmIngestJob.status.in_(["queued", "running"]))
            .order_by(DmIngestJob.id.desc())
            .first()
        )
        if row is None:
            return {"ok": True, "cancelled": False}
        row.cancel_requested = True
        session.commit()
        return {"ok": True, "cancelled": True, "job_id": row.id}


@router.get("/history/export")
def export_history(_: None = Depends(_control_access)):
    store = _store()
    runtime = store.runtime()
    symbol = runtime.resolved_symbol or EXPECTED_LEGACY_SYMBOL
    rows = store.export_rows(symbol)

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "symbol",
                "broker_epoch",
                "received_at",
                "quote_wire",
                "quote_text",
                "precision",
                "last_digit",
                "source",
                "ingestion_id",
                "quality_flags",
            ]
        )
        yield buffer.getvalue()
        for row in rows:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(
                [
                    row.symbol,
                    row.broker_epoch,
                    row.received_at,
                    row.quote_wire,
                    row.quote_text,
                    row.precision,
                    row.last_digit,
                    row.source,
                    row.ingestion_id,
                    row.quality_flags,
                ]
            )
            yield buffer.getvalue()

    return StreamingResponse(generate(), media_type="text/csv")


@router.post("/history/import")
async def import_history(
    file: UploadFile = File(...),
    _: None = Depends(_control_access),
):
    raw = await file.read()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    store = _store()
    ingestion_id = uuid.uuid4().hex
    count = 0
    ignored = []
    if reader.fieldnames:
        ignored = [name for name in reader.fieldnames if name not in {
            "symbol", "broker_epoch", "quote_text", "precision", "source"
        }]
    for record in reader:
        if not record.get("symbol") or not record.get("broker_epoch") or not record.get("quote_text"):
            continue
        precision = record.get("precision")
        observation = normalize_observation(
            symbol=record["symbol"].strip(),
            quote=record["quote_text"].strip(),
            epoch=int(record["broker_epoch"]),
            pip_size=None if precision in {None, ""} else int(precision),
            source="csv",
            ingestion_id=ingestion_id,
            broker_tick_id=None,
        )
        store.add_tick(observation)
        count += 1
    note = "Imported ticks. Any payout column was ignored; historical payout quotes are not invented."
    if ignored:
        note += " Ignored columns: " + ", ".join(ignored)
    store.record_dataset(name=file.filename or "import.csv", source="csv", sha256=digest, row_count=count, note=note)
    store.audit("csv_import", note)
    return {"ok": True, "rows": count, "sha256": digest, "note": note}


@router.post("/history/use-saved")
def use_saved_ticks(_: None = Depends(_control_access)):
    global _copy_running
    with _copy_guard:
        if _copy_running:
            return {
                "ok": True,
                "note": "Copy is already running. The History line updates the count. The page stays usable.",
            }
        store = _store()
        job_id = store.open_touch_copy_job()
        _copy_running = True

    def _run() -> None:
        global _copy_running
        worker_store = _store()
        try:
            result = worker_store.import_saved_ticks("R_100", progress_job_id=job_id)
            worker_store.save_job(
                DmIngestJob,
                job_id,
                status="done",
                ticks_stored=int(result["available"]),
                target_ticks=int(result["available"]),
                note=result["note"],
                error=None,
            )
        except Exception as exc:
            worker_store.save_job(DmIngestJob, job_id, status="error", error=str(exc)[:500])
        finally:
            with _copy_guard:
                _copy_running = False

    threading.Thread(target=_run, name="dm-touch-copy", daemon=True).start()
    return {
        "ok": True,
        "note": "Copy started. The History line shows the count. You can keep using this page.",
    }


@router.post("/train")
def start_train(body: TrainBody, _: None = Depends(_control_access)):
    store = _store()
    job_id = store.enqueue_train(body.enable_mlp)
    store.audit("train_queued", str(job_id))
    return {"ok": True, "job_id": job_id, "promoted": False}


@router.get("/models")
def models(_: None = Depends(_read_access)):
    return {"models": _store().list_models(), "auto_promote": False}


@router.post("/models/{model_id}/promote")
def promote(model_id: int, _: None = Depends(_control_access)):
    store = _store()
    try:
        store.promote(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    store.audit("promote", str(model_id))
    return {"ok": True, "model_id": model_id, "has_demonstrated_edge": False}
