"""Closed-candle scans and durable, opt-in demo execution for R_100."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.config import settings
from app.deriv.client import DerivWSClient
from app.execution.gates import server_execution_allowed
from app.forex.service import captured_closed_candles
from app.synthetic.analysis import analyze_synthetic, chart_overlays
from app.synthetic.trend_confirmation import trend_confirmation
from app.synthetic.execution import execute_demo
from app.synthetic.jobs import synthetic_jobs
from app.synthetic.history import history_root, load_frames, clean_candles
from app.synthetic.ml import ml_status, predict_frames
from app.synthetic.policy import (
    DURATION_SECONDS, MAX_ENTRY_AGE, SYMBOL, TIMEFRAMES, supports_rise_fall, validate_config,
)

# Chart-only. 1m is not an entry timeframe and is not used for training.
CHART_TIMEFRAMES = {"1m": 60, **TIMEFRAMES}
from app.synthetic.store import SyntheticStore
from app.trade_prefs import load_trade_prefs, token_status


def _chart_candles(rows: list[dict], step: int, captured_at: int) -> list[dict]:
    """Closed candles plus the current forming bar so the chart can move live."""
    closed = captured_closed_candles(rows, step, captured_at)
    forming = None
    for row in rows:
        try:
            epoch = int(row["epoch"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if epoch + step > captured_at and (forming is None or epoch >= int(forming["epoch"])):
            forming = dict(row)
    if forming is not None and (not closed or int(forming["epoch"]) > int(closed[-1]["epoch"])):
        closed.append(forming)
    return closed


def synthetic_symbols(rows: list[dict]) -> list[dict]:
    """R_100 is the standard Volatility 100 index, not the 1-second variant."""
    result = []
    for row in rows:
        symbol = row.get("symbol") or row.get("underlying_symbol")
        if symbol != SYMBOL:
            continue
        result.append({
            "symbol": SYMBOL,
            "display_name": row.get("display_name") or row.get("underlying_symbol_name") or "Volatility 100 Index",
            "is_open": row.get("exchange_is_open") in (True, 1, "1")
            and row.get("is_trading_suspended", 0) in (False, 0, "0"),
        })
    return result


def execution_status() -> dict:
    configured = bool(token_status().get("token_configured"))
    allowed = settings.bot_mode == "synthetic" and configured and server_execution_allowed()
    return {
        "allowed": allowed, "token_configured": configured, "demo_only": True,
        "reason": "Demo account and quote are verified before each purchase" if allowed else
        "Set BOT_MODE=synthetic and restart the backend" if settings.bot_mode != "synthetic" else
        "Save a demo token and enable demo execution" if not configured else
        "Enable demo execution in account settings",
    }


class SyntheticService:
    def __init__(self, store: SyntheticStore | None = None):
        self._store = store
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._history_stamp: tuple | None = None
        self._history: dict[str, list[dict]] = {}
        self._quote: dict | None = None
        self._quote_task: asyncio.Task | None = None

    @property
    def store(self) -> SyntheticStore:
        if self._store is None:
            self._store = SyntheticStore(str(Path(settings.model_dir) / "synthetic_r100.sqlite"))
        return self._store

    def status(self) -> dict:
        state = self.store.state()
        count = self.store.daily_count(int(time.time()))
        jobs = synthetic_jobs.snapshot()
        return {
            **state, "analyses": state.get("analyses", []),
            "execution": execution_status(), "daily_count": count,
            "signals_remaining_today": max(0, state["config"]["max_signals_per_day"] - count),
            "signals": self.store.signals(), "ml": ml_status(settings.model_dir),
            **jobs,
            "product": {
                "mode": "synthetic_rise_fall", "symbol": SYMBOL, "duration_seconds": 300,
                "entry_timeframe": "5m", "context_timeframes": ["15m", "1h"],
                "contract": "CALL_PUT_zero_barrier", "timezone": "Asia/Colombo",
            },
        }

    async def symbols(self) -> dict:
        client = DerivWSClient()
        try:
            async with asyncio.timeout(25):
                await client.connect()
                listing = await client.get_active_symbols()
                return {"symbols": synthetic_symbols(listing.get("active_symbols", []))}
        except Exception as exc:
            return {"symbols": [], "error": f"Market discovery unavailable ({type(exc).__name__})"}
        finally:
            await client.disconnect()

    async def chart(self, timeframe: str = "5m", count: int = 500) -> dict:
        if timeframe not in CHART_TIMEFRAMES:
            raise ValueError("Choose 1m, 5m, 15m or 1h")
        step = CHART_TIMEFRAMES[timeframe]
        client = DerivWSClient()
        captured = int(time.time())
        try:
            async with asyncio.timeout(45):
                await client.connect()
                rows = await client.get_candles(SYMBOL, granularity=step, count=max(240, min(count, 1000)))
        finally:
            await client.disconnect()
        rows = _chart_candles(rows, step, captured)
        if not rows:
            raise ValueError("No candles returned by Deriv")
        last_epoch = int(rows[-1]["epoch"])
        forming = last_epoch + step > captured
        return {
            "symbol": SYMBOL, "timeframe": timeframe, "granularity": step,
            "candles": rows, "overlays": chart_overlays(rows),
            "trend_system": trend_confirmation(rows),
            "server_time": int(time.time()),
            "source": "Deriv live candles" if forming else "Deriv closed candles",
            "bar_count": len(rows), "forming": forming,
            "last_candle_age_seconds": max(0, captured - last_epoch),
        }

    async def start(self, updates: dict) -> dict:
        if settings.bot_mode != "synthetic":
            raise ValueError("Set BOT_MODE=synthetic and restart the backend")
        config = validate_config(updates, self.store.state().get("config"))
        self.store.update_state(config=config, running=True, last_error=None)
        self._ensure_loop()
        return self.status()

    async def stop(self) -> dict:
        self.store.update_state(running=False)
        return self.status()

    def quote(self) -> dict:
        """Latest R_100 tick. The chart uses this so the open candle moves without a redraw."""
        self._ensure_quote()
        quote = self._quote
        if not quote:
            return {"symbol": SYMBOL, "epoch": None, "quote": None, "age_seconds": None}
        return {**quote, "age_seconds": max(0, int(time.time()) - int(quote["epoch"]))}

    def _ensure_quote(self) -> None:
        if self._quote_task is None or self._quote_task.done():
            self._quote_task = asyncio.create_task(self._quote_loop())

    async def _quote_loop(self) -> None:
        while True:
            client = DerivWSClient()
            try:
                await client.connect()

                def on_tick(message: dict) -> None:
                    tick = message.get("tick") if isinstance(message, dict) else None
                    if not isinstance(tick, dict):
                        return
                    try:
                        epoch = int(tick["epoch"])
                        price = float(tick["quote"])
                    except (KeyError, TypeError, ValueError):
                        return
                    if epoch > 0 and price > 0:
                        self._quote = {"symbol": SYMBOL, "epoch": epoch, "quote": price}

                await client.subscribe_ticks(SYMBOL, on_tick)
                while client.connected:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def resume(self):
        self._ensure_quote()
        if settings.bot_mode == "synthetic" and self.store.state().get("running"):
            self._ensure_loop()

    def _ensure_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def shutdown(self):
        if self._quote_task:
            self._quote_task.cancel()
            try:
                await self._quote_task
            except asyncio.CancelledError:
                pass
            self._quote_task = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while self.store.state().get("running") and settings.bot_mode == "synthetic":
            await self.scan(execute=True)
            # Check within a minute of candle close, without issuing a setup twice.
            await asyncio.sleep(20)

    async def scan(self, *, execute: bool = False) -> dict:
        if self._lock.locked():
            return self.status()
        async with self._lock:
            owner = self.store.acquire(int(time.time()))
            if owner is None:
                return self.status()
            try:
                async with asyncio.timeout(180):
                    await self._scan(execute=execute)
            except asyncio.TimeoutError:
                self.store.update_state(last_error="Scan timed out. An unconfirmed purchase is never retried.")
            except Exception as exc:
                self.store.update_state(last_error=f"Scan unavailable ({type(exc).__name__})")
            finally:
                self.store.release(owner)
        return self.status()

    async def _scan(self, *, execute: bool = True):
        config = self.store.state()["config"]
        blockers: list[str] = []
        frames: dict[str, list[dict]] = {}
        client = DerivWSClient()
        try:
            await client.connect()
            listing = await client.get_active_symbols()
            markets = synthetic_symbols(listing.get("active_symbols", []))
            if not markets or not markets[0]["is_open"]:
                blockers.append("Volatility 100 is unavailable or suspended")
            else:
                contracts = (await client.get_contracts_for(SYMBOL)).get("contracts_for", {}).get("available", [])
                if not supports_rise_fall(contracts, DURATION_SECONDS):
                    blockers.append("Five-minute Rise/Fall contracts are unavailable")
                for name, step in TIMEFRAMES.items():
                    captured = int(time.time())
                    rows = await client.get_candles(SYMBOL, granularity=step, count=500)
                    frames[name] = captured_closed_candles(rows, step, captured)
        finally:
            await client.disconnect()
        now = int(time.time())
        # Recursive FRAMA/range/EMA state must start at the training origin.
        # A short live-only seed would otherwise change feature values.
        frames = self._with_training_history(frames, now)
        analysis = analyze_synthetic(frames, now=now)
        technical_direction = analysis.get("direction", "NO TRADE")
        prediction = predict_frames(settings.model_dir, frames, now=now, checklist_direction=technical_direction)
        blockers.extend(analysis.get("blockers") or [])
        if prediction.get("hybrid_passed") is not True:
            blockers.append(prediction.get("reason") or "A validated CatBoost model must agree")
        decision = int(analysis.get("decision_epoch") or 0)
        if not 0 <= now - decision <= MAX_ENTRY_AGE:
            blockers.append("Wait for the next 5-minute close: entry window is 60 seconds")
        analysis.update(
            ml=prediction, technical_direction=technical_direction, blockers=list(dict.fromkeys(blockers)),
            eligible=not blockers and technical_direction in {"RISE", "FALL"},
            direction=technical_direction if not blockers else "NO TRADE",
        )
        self.store.update_state(analyses=[analysis], last_scan=now, last_error=None)
        if not execute or not analysis["eligible"] or not self.store.state().get("running"):
            return
        if settings.bot_mode != "synthetic":
            return
        if config["demo_auto_trade"] and not execution_status()["allowed"]:
            self.store.update_state(last_error=execution_status()["reason"])
            return
        probability = prediction.get("p_rise") if technical_direction == "RISE" else prediction.get("p_fall")
        signal = {
            "symbol": SYMBOL, "direction": technical_direction, "duration_seconds": DURATION_SECONDS,
            "created_epoch": now, "decision_epoch": decision, "expiry_epoch": now + DURATION_SECONDS,
            "setup_key": f"{SYMBOL}:{decision}", "stake": config["stake"], "probability": probability,
        }
        limit = float(load_trade_prefs().get("daily_loss_limit") or 0)
        reserved, reason = self.store.reserve(signal, now, expected_config=config, stake_budget=limit if limit > 0 else None)
        if not reserved:
            self.store.update_state(last_error=reason)
            return
        if config["demo_auto_trade"]:
            def can_execute() -> bool:
                state = self.store.state()
                return (
                    state.get("running") is True and state.get("config") == config
                    and settings.bot_mode == "synthetic"
                    and 0 <= time.time() - decision <= MAX_ENTRY_AGE
                )

            result = await execute_demo({**reserved, "_can_execute": can_execute}, stake=config["stake"])
            self.store.finish(reserved["id"], result)

    def _with_training_history(self, frames: dict, now: int) -> dict:
        root = history_root(settings.model_dir)
        paths = [root / f"{name}.parquet" for name in TIMEFRAMES]
        stamp = tuple((str(path), path.stat().st_mtime_ns if path.exists() else None) for path in paths)
        if stamp != self._history_stamp:
            self._history = load_frames(settings.model_dir)
            self._history_stamp = stamp
        return {
            name: clean_candles(self._history.get(name, []) + frames.get(name, []), step, now=now)
            for name, step in TIMEFRAMES.items()
        }


synthetic_service = SyntheticService()
