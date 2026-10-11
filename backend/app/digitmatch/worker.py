"""Market-data and demo-execution worker. Training runs in a different process."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import structlog

from app.config import settings
from app.digitmatch.account import AccountRejected, require_trade_scope, verify_demo_authorize
from app.digitmatch.credentials import resolve_digitmatch_credentials
from app.digitmatch.shared_session import DigitMatchSession
from app.digitmatch.errors import DerivCallError
from app.digitmatch.execution import CycleState, apply_settlement, chosen_digit, reconcile_unknown, run_cycle
from app.digitmatch.features import LOOKBACK
from app.digitmatch.ingestion import normalize_observation
from app.digitmatch.instrument import MarketUnavailable, resolve_volatility_100, validate_digitmatch_five_ticks
from app.digitmatch.prediction import predict_latest
from app.digitmatch.store import SqlStore
from app.models.digitmatch import DmContract, DmIngestJob, DmIntent

logger = structlog.get_logger(__name__)
_BLOCKING = {"gap", "conflict", "out_of_order", "precision_change", "replay", "precision_unknown", "digit_unreadable"}


def _usable(flags: str, digit) -> bool:
    found = set(filter(None, (flags or "").split(",")))
    return digit is not None and not (found & _BLOCKING)


class LiveBroker:
    def __init__(self, adapter: DigitMatchSession, symbol: str, auth: dict):
        self.adapter = adapter
        self.symbol = symbol
        self.auth = auth

    async def authorize_payload(self) -> dict:
        return self.auth

    async def proposal(self, digit: int, stake: float, currency: str) -> dict:
        return await self.adapter.proposal(symbol=self.symbol, digit=digit, stake=stake, currency=currency)

    async def buy(self, proposal_id: str, price: float) -> dict:
        return await self.adapter.buy(proposal_id, price)


def _latest_epoch(store: SqlStore, symbol: str) -> int | None:
    latest = store.latest_tick(symbol)
    if latest is None:
        return None
    return int(latest.broker_epoch)


def _live_controls(store: SqlStore) -> dict:
    runtime = store.runtime()
    return {
        "mode": runtime.mode,
        "paused": bool(runtime.paused),
        "emergency_stop": bool(runtime.emergency_stop),
        "uncertain": bool(runtime.uncertain_block),
    }


def _persist(store: SqlStore, state: CycleState, result: dict) -> None:
    probs = result.get("probabilities")
    store.record_decision(
        mode=state.mode,
        action=result.get("action") or "skip",
        reason=str(result.get("reason") or ""),
        digit=result.get("digit"),
        probabilities=probs,
        break_even=result.get("break_even"),
        expected_value=result.get("expected_value"),
        ask_price=result.get("ask_price"),
        total_payout=result.get("total_payout"),
        tick_epoch=state.latest_tick_epoch,
    )
    if state.uncertain:
        store.update_runtime(uncertain_block=True, uncertain_reason=state.uncertain_reason)
    for intent in state.intents:
        store.commit_intent(intent)
    for contract in state.contracts:
        with store.Session() as session:
            session.add(
                DmContract(
                    broker_contract_id=str(contract["broker_contract_id"]),
                    digit=int(contract["digit"]),
                    status=contract.get("status") or "open",
                    buy_price=contract.get("buy_price"),
                    total_payout=contract.get("total_payout"),
                    mode=contract.get("mode") or state.mode,
                )
            )
            session.commit()


def _save_live_tick(store: SqlStore, observation, previous_epoch, previous_precision, previous_quote, replay: bool) -> None:
    store.add_tick(
        observation,
        previous_epoch=previous_epoch,
        previous_precision=previous_precision,
        previous_quote=previous_quote,
        replay=replay,
    )
    store.update_runtime(last_tick_epoch=observation.broker_epoch, last_tick_received_at=observation.received_at)


def _save_history_page(store: SqlStore, symbol: str, ordered: list[dict], ingestion_id: str) -> int:
    """Save one history page off the price-socket task so ping replies can still be read."""
    predecessor = store.tick_before(symbol, int(ordered[0]["epoch"]))
    previous_epoch = None if predecessor is None else predecessor.broker_epoch
    previous_precision = None if predecessor is None else predecessor.precision
    previous_quote = None if predecessor is None else predecessor.quote_text
    inserted = 0
    for item in ordered:
        observation = normalize_observation(
            symbol=symbol,
            quote=item["quote"],
            epoch=int(item["epoch"]),
            pip_size=item.get("pip_size"),
            source="history",
            ingestion_id=ingestion_id,
            broker_tick_id=item.get("id"),
        )
        result = store.add_tick(
            observation,
            previous_epoch=previous_epoch,
            previous_precision=previous_precision,
            previous_quote=previous_quote,
        )
        previous_epoch = observation.broker_epoch
        previous_precision = observation.precision
        previous_quote = observation.quote_text
        if result != "duplicate":
            inserted += 1
    return inserted


async def _history_once(adapter: DigitMatchSession, store: SqlStore, symbol: str) -> None:
    job = store.next_job(DmIngestJob)
    if not job or job.get("symbol") not in {None, symbol}:
        return
    store.save_job(DmIngestJob, job["id"], status="running")
    end = job.get("checkpoint_end") or "latest"
    target = int(job["target_ticks"])
    pages = 0
    note = (
        "The configured target is a download goal, not evidence that the sample "
        "is statistically sufficient. The broker may return fewer ticks."
    )
    try:
        while store.tick_count(symbol) < target:
            fresh = store.job_flags(DmIngestJob, job["id"])
            if fresh and fresh.get("cancel_requested"):
                store.save_job(DmIngestJob, job["id"], status="cancelled", note=note)
                return
            try:
                rows = await adapter.ticks_history(
                    symbol, count=settings.dm_history_page_size, end=end if end != "latest" else "latest"
                )
            except DerivCallError as exc:
                if exc.code == "RateLimit":
                    await asyncio.sleep(min(60, 2 ** min(pages, 5)))
                    continue
                raise
            if not rows:
                note += " Broker returned no further ticks."
                break
            ordered = sorted(rows, key=lambda item: int(item["epoch"]))
            if not ordered:
                break
            inserted = await asyncio.to_thread(
                _save_history_page, store, symbol, ordered, job["ingestion_id"]
            )
            if inserted == 0:
                break
            oldest = min(int(item["epoch"]) for item in ordered)
            end = oldest - 1
            pages += 1
            store.save_job(
                DmIngestJob,
                job["id"],
                ticks_stored=store.tick_count(symbol),
                pages=pages,
                checkpoint_end=str(end),
                note=note,
            )
        await asyncio.to_thread(store.recompute_order_flags, symbol)
        store.save_job(
            DmIngestJob,
            job["id"],
            status="done",
            ticks_stored=store.tick_count(symbol),
            note=note,
        )
    except Exception as exc:
        store.save_job(DmIngestJob, job["id"], status="error", error=str(exc)[:500], note=note)
        logger.warning("dm_history_failed", error=type(exc).__name__)


async def _monitor(adapter: DigitMatchSession, store: SqlStore) -> None:
    with store.Session() as session:
        rows = session.query(DmContract).filter_by(status="open").all()
        open_rows = [(row.broker_contract_id, row.id) for row in rows]
    for contract_id, row_id in open_rows:
        try:
            update = await adapter.open_contract(contract_id)
        except Exception as exc:
            logger.warning("dm_monitor_failed", error=type(exc).__name__)
            continue
        current = {"status": "open"}
        apply_settlement(current, update)
        values = {
            "status": current["status"],
            "entry_spot": None if update.get("entry_tick") is None else str(update.get("entry_tick")),
            "exit_spot": None if update.get("exit_tick") is None else str(update.get("exit_tick")),
            "longcode": update.get("longcode"),
        }
        closed_profit = None
        if current["status"] != "open":
            profit = update.get("profit")
            values["profit"] = None if profit is None else float(profit)
            values["sell_time"] = datetime.now(timezone.utc)
            closed_profit = values["profit"]
        with store.Session() as session:
            from sqlalchemy import update as sql_update

            session.execute(sql_update(DmContract).where(DmContract.id == row_id).values(**values))
            session.commit()
        if closed_profit is not None:
            store.apply_recovery(float(closed_profit))


async def _reconcile(adapter: DigitMatchSession, store: SqlStore) -> None:
    with store.Session() as session:
        pending = session.query(DmIntent).filter_by(status="uncertain").all()
        intents = [
            {
                "id": row.client_key,
                "status": row.status,
                "digit": row.digit,
                "ask_price": row.ask_price,
                "total_payout": row.total_payout,
                "symbol": row.symbol or "R_100",
                "duration_ticks": row.duration_ticks or 5,
                "feature_epoch": row.feature_epoch,
                "mode": row.mode or "demo_explore",
            }
            for row in pending
        ]
    if not intents:
        return
    state = CycleState(uncertain=True)
    state.intents = intents
    try:
        statement = await adapter.statement()
    except Exception:
        store.update_runtime(uncertain_block=True, uncertain_reason="could not read the broker statement")
        return
    result = reconcile_unknown(state, statement)
    store.audit("reconcile", result["status"])
    for intent in state.intents:
        store.commit_intent(intent)
    if result["status"] != "reconciled":
        store.update_runtime(uncertain_block=True, uncertain_reason="purchase outcome is still uncertain")
        return
    for contract in state.contracts:
        with store.Session() as session:
            exists = (
                session.query(DmContract)
                .filter_by(broker_contract_id=str(contract["broker_contract_id"]))
                .one_or_none()
            )
            if exists is None:
                session.add(
                    DmContract(
                        broker_contract_id=str(contract["broker_contract_id"]),
                        digit=int(contract["digit"]),
                        status=contract.get("status") or "open",
                        buy_price=contract.get("buy_price"),
                        total_payout=contract.get("total_payout"),
                        mode=contract.get("mode") or "demo_explore",
                    )
                )
                session.commit()
    store.update_runtime(uncertain_block=False, uncertain_reason=None)


def _series_arrays(store: SqlStore, symbol: str, limit: int | None = None):
    rows = store.series_tail(symbol, limit) if limit else store.series(symbol)
    if not rows:
        return None
    digits = []
    prices = []
    usable = []
    epochs = []
    for row in rows:
        if row.digit_value is None:
            continue
        digits.append(int(row.digit_value))
        try:
            prices.append(float(row.quote_text))
        except ValueError:
            continue
        usable.append(_usable(row.quality_flags, row.digit_value))
        epochs.append(int(row.broker_epoch))
    if not digits:
        return None
    return np.asarray(digits), np.asarray(prices), np.asarray(usable), np.asarray(epochs)


def _recent_digits(arrays) -> list[int]:
    if arrays is None:
        return []
    recent = [int(digit) for digit, ok in zip(arrays[0].tolist(), arrays[2].tolist()) if ok]
    return recent[-100:]


async def run() -> None:
    store = SqlStore()
    store.create_schema()
    store.seed()
    owner = f"dm-{uuid.uuid4()}"
    last_skip = ""
    last_skip_at = 0.0
    while True:
        creds = resolve_digitmatch_credentials()
        if not creds["configured"]:
            store.update_runtime(connection_status="offline", auth_status="unconfigured", demo_verified=False)
            logger.info("dm_credentials_missing")
            await asyncio.sleep(5)
            continue
        try:
            adapter = DigitMatchSession(creds["token"], creds["app_id"])
        except RuntimeError as exc:
            store.update_runtime(
                connection_status="offline",
                auth_status="unconfigured",
                demo_verified=False,
                last_error=str(exc)[:300],
            )
            await asyncio.sleep(5)
            continue
        try:
            await adapter.connect()
            auth = verify_demo_authorize(await adapter.authorize())
            require_trade_scope(auth["scopes"])
            resolved = resolve_volatility_100(await adapter.active_symbols())
            spec = validate_digitmatch_five_ticks(await adapter.contracts_for(resolved["symbol"], auth["currency"] or "USD"))
            store.update_runtime(
                connection_status="online",
                auth_status="demo",
                demo_verified=True,
                loginid=auth["loginid"],
                currency=auth["currency"],
                balance=None if auth.get("balance") is None else float(auth["balance"]),
                resolved_symbol=resolved["symbol"],
                resolved_display=resolved["display_name"],
                matches_legacy_symbol=resolved["matches_legacy_symbol"],
                contract_ready=True,
                instrument_error=None,
                contract_error=None,
                broker_min_stake=None if spec.get("min_stake") is None else float(spec["min_stake"]),
                last_error=None,
            )
            symbol = resolved["symbol"]
            known_pip = resolved.get("pip")
            store.recover_if_unowned(datetime.now(timezone.utc))

            async def on_tick(tick: dict) -> None:
                if tick.get("symbol") and tick.get("symbol") != symbol:
                    return
                observation = normalize_observation(
                    symbol=symbol,
                    quote=tick["quote"],
                    epoch=tick["epoch"],
                    pip_size=tick.get("pip_size") if tick.get("pip_size") is not None else known_pip,
                    source="live",
                    ingestion_id="live",
                    broker_tick_id=tick.get("id"),
                )
                prev = store.latest_tick(symbol)
                replay = (
                    prev is not None
                    and prev.broker_epoch == observation.broker_epoch
                    and prev.quote_text == observation.quote_text
                    and not observation.broker_tick_id
                )
                await asyncio.to_thread(
                    _save_live_tick,
                    store,
                    observation,
                    None if prev is None else prev.broker_epoch,
                    None if prev is None else prev.precision,
                    None if prev is None else prev.quote_text,
                    replay,
                )

            await adapter.subscribe_ticks(symbol, on_tick)
            await _reconcile(adapter, store)
            broker = LiveBroker(adapter, symbol, auth)
            live_tail = LOOKBACK + 200
            while adapter.connected:
                await _history_once(adapter, store, symbol)
                await _monitor(adapter, store)
                runtime = store.runtime()
                active = store.active_model()
                model_path = None if active is None else active.path
                created_at = None if active is None else active.created_at
                arrays = await asyncio.to_thread(_series_arrays, store, symbol, live_tail)
                feature_epoch = None
                if arrays is not None and len(arrays[3]):
                    feature_epoch = int(arrays[3][-1])
                probabilities = None
                features_ready = False
                model_ready = False
                model_expired = False
                if arrays is not None and model_path:
                    age_hours = 0
                    if created_at is not None:
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
                    model_expired = age_hours > settings.dm_model_max_age_hours
                    try:
                        probabilities = await asyncio.to_thread(
                            predict_latest, model_path, arrays[0], arrays[1], arrays[2]
                        )
                        features_ready = probabilities is not None
                        model_ready = True
                    except Exception as exc:
                        logger.warning("dm_predict_failed", error=type(exc).__name__)
                elif arrays is not None and len(arrays[0]) < LOOKBACK:
                    features_ready = False
                latest = store.latest_tick(symbol)
                runtime = store.runtime()
                received = runtime.last_tick_received_at
                if received is None and latest is not None:
                    received = latest.received_at
                if received is not None and received.tzinfo is None:
                    received = received.replace(tzinfo=timezone.utc)
                locked = False
                if (
                    runtime.mode != "observe"
                    and not runtime.paused
                    and not runtime.emergency_stop
                    and not runtime.uncertain_block
                ):
                    locked = store.try_lock(owner, datetime.now(timezone.utc), ttl_seconds=120)
                    if not locked:
                        await asyncio.sleep(1)
                        continue
                runtime = store.runtime()
                state = CycleState(
                    mode=runtime.mode,
                    probabilities=None if probabilities is None else probabilities.tolist(),
                    latest_tick_epoch=None if latest is None else latest.broker_epoch,
                    feature_epoch=feature_epoch,
                    tick_received_at=received,
                    features_ready=features_ready,
                    model_ready=model_ready,
                    model_expired=model_expired,
                    owner=owner,
                    symbol=symbol,
                    execution_eligible=bool(active is not None and active.execution_eligible),
                    proposal_spacing_seconds=float(settings.dm_proposal_spacing_seconds),
                    max_tick_age_seconds=settings.dm_max_tick_age_seconds,
                    max_proposal_age_seconds=settings.dm_max_proposal_age_seconds,
                    margin=float(runtime.margin),
                    uncertain=bool(runtime.uncertain_block),
                    risk=store.risk_snapshot(datetime.now(timezone.utc)),
                    live_epoch=lambda: _latest_epoch(store, symbol),
                    live_controls=lambda: _live_controls(store),
                    fresh_risk=lambda: store.risk_snapshot(datetime.now(timezone.utc)),
                    commit_intent=store.commit_intent,
                    recent_digits=_recent_digits(arrays),
                    last_chosen_digit=store.latest_decision_digit(),
                )
                try:
                    result = await run_cycle(broker, state, datetime.now(timezone.utc))
                except Exception as exc:
                    logger.warning("dm_cycle_failed", error=type(exc).__name__)
                    result = {
                        "action": "skip",
                        "reason": "cycle_failed",
                        "probabilities": state.probabilities,
                        "digit": chosen_digit(state) if state.probabilities else None,
                    }
                try:
                    interesting = result.get("action") != "skip"
                    signature = f"{result.get('action')}:{result.get('reason')}:{result.get('digit')}:{state.latest_tick_epoch}"
                    now_s = time.time()
                    if interesting or signature != last_skip or now_s - last_skip_at > 15 or state.intents:
                        _persist(store, state, result)
                        last_skip = signature
                        last_skip_at = now_s
                finally:
                    if locked:
                        store.release_lock(owner)
                try:
                    bal = await adapter.balance()
                    if bal.get("balance") is not None:
                        store.update_runtime(balance=float(bal["balance"]), currency=bal.get("currency") or runtime.currency)
                except Exception:
                    pass
                await asyncio.sleep(1)
            store.update_runtime(connection_status="reconnecting", last_error="price socket closed")
        except AccountRejected as exc:
            store.update_runtime(
                connection_status="online",
                auth_status="real_rejected",
                demo_verified=False,
                last_error=str(exc),
                contract_ready=False,
            )
            logger.warning("dm_account_rejected", reason=str(exc))
            await asyncio.sleep(15)
        except MarketUnavailable as exc:
            store.update_runtime(contract_ready=False, contract_error=str(exc), instrument_error=str(exc), last_error=str(exc))
            logger.warning("dm_market_unavailable", reason=str(exc))
            await asyncio.sleep(15)
        except Exception as exc:
            store.update_runtime(connection_status="reconnecting", last_error=str(exc)[:300])
            logger.warning("dm_worker_error", error=type(exc).__name__)
            await asyncio.sleep(5)
        finally:
            await adapter.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
