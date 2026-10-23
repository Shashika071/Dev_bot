"""Closed-candle scans and durable, opt-in demo execution for R_100."""
from __future__ import annotations

import asyncio
import copy
import math
import threading
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
from app.trading_ml.service import research_status
from app.synthetic.policy import (
    DURATION_SECONDS, MAX_ENTRY_AGE, SYMBOL, TIMEFRAMES, supports_rise_fall, validate_config,
)

# Entries use the closed 1m candle. 5m and 15m confirm the direction.
CHART_TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
QUOTE_STALE_SECONDS = 15
TICK_CLOCK_SKEW_SECONDS = 2
CONFIDENCE_STALE_SECONDS = 90
from app.synthetic.store import SyntheticStore
from app.synthetic.evidence import STRATEGY_VERSION
from app.trade_prefs import load_trade_prefs, token_status


def _chart_candles(rows: list[dict], step: int, captured_at: int) -> list[dict]:
    """Closed candles plus the current forming bar so the chart can move live."""
    # Validate the open bar with the same OHLC rules, but allow its future close.
    # A timestamp beyond the request's current interval is never a forming bar.
    current_open = captured_at // step * step
    return clean_candles(rows, step, now=current_open + step)


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


_RETIRED_MODEL_TEXT = ("catboost", "trained model", "promoted model", "model must agree", "did not pass validation")


def _without_retired_model(analysis: dict) -> dict:
    """Drop the removed one-minute model from a stored decision."""
    if not isinstance(analysis, dict):
        return analysis
    original = list(analysis.get("blockers") or [])
    blockers = [
        item for item in original
        if not (isinstance(item, str) and any(marker in item.lower() for marker in _RETIRED_MODEL_TEXT))
    ]
    cleaned = {key: value for key, value in analysis.items() if key != "ml"}
    removed = len(blockers) != len(original)
    cleaned["blockers"] = blockers
    if not removed:
        return cleaned
    technical = cleaned.get("technical_direction")
    if technical not in {"RISE", "FALL"}:
        technical = "NO TRADE"
    cleaned["eligible"] = not blockers and technical in {"RISE", "FALL"}
    cleaned["direction"] = technical if cleaned["eligible"] else "NO TRADE"
    return cleaned


def execution_status() -> dict:
    configured = bool(token_status().get("token_configured"))
    allowed = settings.bot_mode == "synthetic" and configured and server_execution_allowed()
    return {
        "allowed": False, "account_ready": allowed, "token_configured": configured, "demo_only": True,
        "reason": "The one-minute technical strategy has no validated win probability. Use monitoring to collect paper outcomes; demo purchases remain blocked." if allowed else
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
        self._confidence: dict | None = None
        self._confidence_task: asyncio.Task | None = None
        self._confidence_error: str | None = None
        self._quote_error: str | None = None
        self._quote_received_at: float | None = None
        self._analysis_lock = threading.Lock()
        self._analysis_cache_key: tuple | None = None
        self._analysis_cache: dict | None = None
        self._confidence_refresh_key: tuple | None = None
        self._confidence_retry_at = 0
        self._research_ticks: list[dict] = []
        self._research_flush_at = 0.0
        self._market_lock = asyncio.Lock()
        self._chart_cache: dict[str, tuple[float, dict]] = {}

    @property
    def store(self) -> SyntheticStore:
        if self._store is None:
            self._store = SyntheticStore(str(Path(settings.model_dir) / "synthetic_r100.sqlite"))
        return self._store

    def status(self) -> dict:
        state = self.store.state()
        count = self.store.daily_count(int(time.time()))
        jobs = synthetic_jobs.snapshot()
        analyses = [_without_retired_model(item) for item in state.get("analyses", [])]
        return {
            **state, "analyses": analyses,
            "confidence": self._confidence_snapshot(state),
            "execution": execution_status(), "daily_count": count,
            "signals_remaining_today": max(0, state["config"]["max_signals_per_day"] - count),
            "signals": self.store.signals(),
            "performance": self.store.performance(),
            "research": research_status(
                settings.model_dir,
                quote_epoch=self._quote.get("epoch") if isinstance(self._quote, dict) else None,
            ),
            **jobs,
            "product": {
                "mode": "synthetic_rise_fall", "symbol": SYMBOL, "duration_seconds": DURATION_SECONDS,
                "entry_timeframe": "1m", "context_timeframes": ["5m", "15m"],
                "contract": "CALL_PUT_zero_barrier", "timezone": "Asia/Colombo",
            },
        }

    def _cached_chart(self, timeframe: str) -> dict | None:
        cached = self._chart_cache.get(timeframe)
        if cached is None:
            return None
        return {**cached[1], "server_time": int(time.time())}

    async def symbols(self) -> dict:
        client = DerivWSClient()
        try:
            async with self._market_lock:
                async with asyncio.timeout(20):
                    await client.connect()
                    listing = await client.get_active_symbols()
                    return {"symbols": synthetic_symbols(listing.get("active_symbols", []))}
        except Exception as exc:
            return {"symbols": [], "error": f"Market discovery unavailable ({type(exc).__name__})"}
        finally:
            await client.disconnect()

    async def chart(self, timeframe: str = "5m", count: int = 500) -> dict:
        if timeframe not in CHART_TIMEFRAMES:
            raise ValueError("Choose 1m, 5m, 15m, 1h or 1d")
        step = CHART_TIMEFRAMES[timeframe]
        cached = self._chart_cache.get(timeframe)
        if cached and (time.monotonic() - cached[0] < 12 or self._market_lock.locked()):
            return self._cached_chart(timeframe)
        client: DerivWSClient | None = None
        rows: list[dict] = []
        captured = int(time.time())
        try:
            async with self._market_lock:
                cached = self._chart_cache.get(timeframe)
                if cached and time.monotonic() - cached[0] < 12:
                    return self._cached_chart(timeframe)
                client = DerivWSClient()
                async with asyncio.timeout(12):
                    await client.connect()
                    captured = int(time.time())
                    rows = await client.get_candles(SYMBOL, granularity=step, count=max(240, min(count, 1000)))
        except Exception:
            cached_body = self._cached_chart(timeframe)
            if cached_body is not None:
                return cached_body
            raise
        finally:
            if client is not None:
                await client.disconnect()
        rows = _chart_candles(rows, step, captured)
        if not rows:
            cached_body = self._cached_chart(timeframe)
            if cached_body is not None:
                return cached_body
            raise ValueError("No candles returned by Deriv")
        last_epoch = int(rows[-1]["epoch"])
        forming = last_epoch + step > captured
        payload = {
            "symbol": SYMBOL, "timeframe": timeframe, "granularity": step,
            "candles": rows, "overlays": chart_overlays(rows),
            "trend_system": trend_confirmation(rows),
            "server_time": int(time.time()),
            "snapshot_epoch": captured,
            "source": "Deriv live candles" if forming else "Deriv closed candles",
            "bar_count": len(rows), "forming": forming,
            "last_candle_age_seconds": max(0, captured - last_epoch),
        }
        self._chart_cache[timeframe] = (time.monotonic(), payload)
        return payload

    async def start(self, updates: dict) -> dict:
        if settings.bot_mode != "synthetic":
            raise ValueError("Set BOT_MODE=synthetic and restart the backend")
        config = validate_config(updates, self.store.state().get("config"))
        if config["demo_auto_trade"] and not execution_status()["allowed"]:
            raise ValueError(execution_status()["reason"])
        self.store.update_state(config=config, running=True, last_error=None)
        self._ensure_loop()
        return await asyncio.to_thread(self.status)

    async def stop(self) -> dict:
        self.store.update_state(running=False)
        return await asyncio.to_thread(self.status)

    def quote(self) -> dict:
        """Latest R_100 tick. The chart uses this so the open candle moves without a redraw."""
        self._ensure_quote()
        quote = self._quote
        now = int(time.time())
        if not quote:
            return {"symbol": SYMBOL, "epoch": None, "quote": None, "age_seconds": None,
                    "status": "error" if self._quote_error else "connecting", "stale": True,
                    "error": self._quote_error, "server_time": now}
        age = max(0, now - int(quote["epoch"]))
        stale = age > QUOTE_STALE_SECONDS
        return {**quote, "age_seconds": age, "stale": stale,
                "status": "stale" if stale else "live", "error": self._quote_error,
                "server_time": now}

    def confidence(self) -> dict:
        """Latest model rise/fall probabilities for the chart. Does not place a trade."""
        self._ensure_confidence()
        return self._confidence_snapshot()

    def _confidence_snapshot(self, state: dict | None = None) -> dict:
        """Reading a cached prediction must never make an old prediction fresh."""
        body = dict(self._confidence) if self._confidence else None
        if body is None:
            state = self.store.state() if state is None else state
            analyses = state.get("analyses") or []
            analysis = analyses[0] if analyses and isinstance(analyses[0], dict) else {}
            body = confidence_view(analysis, updated_at=state.get("last_scan") or None)
        now = int(time.time())
        updated = body.get("updated_at")
        age = max(0, now - updated) if isinstance(updated, int) else None
        decision = body.get("decision_epoch")
        decision_age = now - decision if isinstance(decision, int) else None
        has_probabilities = body.get("combined_p_rise") is not None and body.get("combined_p_fall") is not None
        stale = age is None or age >= CONFIDENCE_STALE_SECONDS or (
            has_probabilities and (decision_age is None or not 0 <= decision_age < CONFIDENCE_STALE_SECONDS)
        )
        training = synthetic_jobs.train_job.get("status") == "running"
        status = ("training" if training else "error" if self._confidence_error else
                  "stale" if has_probabilities and stale else "ready" if has_probabilities else
                  "loading" if updated is None else "unavailable")
        return {**body, "age_seconds": age, "stale": stale, "status": status,
                "error": self._confidence_error, "server_time": now}

    def _ensure_confidence(self) -> None:
        if self._confidence_task is None or self._confidence_task.done():
            self._confidence_task = asyncio.create_task(self._confidence_loop())

    async def _confidence_loop(self) -> None:
        while True:
            try:
                await self._refresh_confidence()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._confidence_error = f"Confidence refresh unavailable ({type(exc).__name__}); retrying"
            await asyncio.sleep(4)

    async def _refresh_confidence(self) -> None:
        if synthetic_jobs.train_job.get("status") == "running":
            return
        now = int(time.time())
        refresh_key = (now // TIMEFRAMES["1m"], self._artifact_stamp())
        if self._confidence is not None and self._confidence_refresh_key == refresh_key and not self._confidence_error:
            return
        if now < self._confidence_retry_at:
            return
        state = self.store.state()
        analyses = state.get("analyses") or []
        last_scan = int(state.get("last_scan") or 0)
        if analyses and isinstance(analyses[0], dict) and 0 <= int(time.time()) - last_scan <= 8:
            view = confidence_view(analyses[0], updated_at=last_scan)
            if view.get("combined_p_rise") is not None and view.get("combined_p_fall") is not None:
                self._confidence = view
                self._confidence_error = None
                self._confidence_refresh_key = refresh_key
                return
        frames: dict[str, list[dict]] = {}
        client = DerivWSClient()
        try:
            async with self._market_lock:
                async with asyncio.timeout(20):
                    await client.connect()
                    for name, step in TIMEFRAMES.items():
                        captured = int(time.time())
                        rows = await client.get_candles(SYMBOL, granularity=step, count=500)
                        frames[name] = captured_closed_candles(rows, step, captured)
        finally:
            await client.disconnect()
        now = int(time.time())
        analysis = await asyncio.to_thread(self._analyze_frames, frames, now)
        view = confidence_view(analysis)
        self._confidence = view
        self._confidence_error = None
        # Keep a real rise/fall reading for the rest of this minute. A blank reading is retried shortly.
        if (view.get("combined_p_rise") is not None and view.get("combined_p_fall") is not None
                and analysis.get("decision_epoch") == now // TIMEFRAMES["1m"] * TIMEFRAMES["1m"]):
            self._confidence_retry_at = 0
            self._confidence_refresh_key = refresh_key
        else:
            self._confidence_retry_at = now + 15

    def _artifact_stamp(self) -> tuple:
        root = history_root(settings.model_dir).parent
        paths = [root / "active.json", root / "latest_report.json"]
        paths.extend(root / "history" / f"{name}.parquet" for name in TIMEFRAMES)
        return tuple((str(path), path.stat().st_mtime_ns if path.exists() else None) for path in paths)

    def _analyze_frames(self, frames: dict, now: int) -> dict:
        """CPU/disk work lives in a worker so subscriptions and HTTP stay responsive."""
        with self._analysis_lock:
            key = (now // TIMEFRAMES["1m"], self._artifact_stamp(),
                   synthetic_jobs.train_job.get("status"),
                   tuple((name, tuple(tuple(sorted(row.items())) for row in frames.get(name, [])))
                         for name in TIMEFRAMES))
            if key == self._analysis_cache_key and self._analysis_cache is not None:
                return copy.deepcopy(self._analysis_cache)
            frames = self._with_training_history(frames, now)
            analysis = analyze_synthetic(frames, now=now)
            self._analysis_cache_key = key
            self._analysis_cache = copy.deepcopy(analysis)
            return analysis

    def _accept_tick(self, message: dict) -> None:
        tick = message.get("tick") if isinstance(message, dict) else None
        if not isinstance(tick, dict) or tick.get("symbol", SYMBOL) != SYMBOL:
            return
        try:
            raw_epoch = float(tick["epoch"])
            price = float(tick["quote"])
            if isinstance(tick["epoch"], bool) or isinstance(tick["quote"], bool):
                return
            if not math.isfinite(raw_epoch) or not raw_epoch.is_integer() or not math.isfinite(price):
                return
            epoch = int(raw_epoch)
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        now = time.time()
        # Deriv's tick clock runs a fraction of a second ahead of this host.
        # Dropping those prints freezes the candle until the feed is torn down.
        if price <= 0 or epoch - now > TICK_CLOCK_SKEW_SECONDS or now - epoch > QUOTE_STALE_SECONDS:
            return
        if self._quote is not None and epoch < self._quote["epoch"]:
            return
        self._quote_received_at = time.monotonic()
        if self._quote is not None and epoch == self._quote["epoch"] and price == self._quote["quote"]:
            return
        self._quote = {"symbol": SYMBOL, "epoch": epoch, "quote": price, "updated_at": int(now)}
        self._quote_error = None
        if self._research_ticks and self._research_ticks[-1]["epoch"] == epoch:
            self._research_ticks[-1]["quote"] = price
        else:
            self._research_ticks.append({"epoch": epoch, "quote": price})
        if len(self._research_ticks) > 5000:
            self._research_ticks = self._research_ticks[-5000:]

    def _ensure_quote(self) -> None:
        if self._quote_task is None or self._quote_task.done():
            self._quote_task = asyncio.create_task(self._quote_loop())

    async def _quote_loop(self) -> None:
        while True:
            client = DerivWSClient()
            try:
                async with asyncio.timeout(20):
                    await client.connect()
                    await client.subscribe_ticks(SYMBOL, self._accept_tick)
                subscribed_at = time.monotonic()
                while client.connected:
                    await asyncio.sleep(1)
                    if time.monotonic() - self._research_flush_at >= 30:
                        await self._flush_research_ticks()
                    if time.monotonic() - max(subscribed_at, self._quote_received_at or 0) > QUOTE_STALE_SECONDS:
                        raise TimeoutError("No fresh R_100 ticks received")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._quote_error = f"Live tick feed unavailable ({type(exc).__name__}); reconnecting"
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(2)

    async def _flush_research_ticks(self) -> None:
        rows = self._research_ticks
        if not rows:
            self._research_flush_at = time.monotonic()
            return
        snapshot = list(rows)
        self._research_ticks = []
        self._research_flush_at = time.monotonic()

        def write() -> None:
            from app.trading_ml.data.ticks import append_live_ticks
            from app.trading_ml.service import research_root
            append_live_ticks(research_root(settings.model_dir) / "live_ticks.parquet", snapshot)
            self.store.observe_ticks(snapshot, int(time.time()))

        try:
            await asyncio.to_thread(write)
        except Exception:
            self._research_ticks = (snapshot + self._research_ticks)[-5000:]

    async def resume(self):
        self._ensure_quote()
        self._ensure_confidence()
        if settings.bot_mode == "synthetic" and self.store.state().get("running"):
            self._ensure_loop()

    def _ensure_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def shutdown(self):
        if self._confidence_task:
            self._confidence_task.cancel()
            try:
                await self._confidence_task
            except asyncio.CancelledError:
                pass
            self._confidence_task = None
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
        await self._flush_research_ticks()

    async def _loop(self):
        while self.store.state().get("running") and settings.bot_mode == "synthetic":
            await self.scan(execute=True)
            # Check within a minute of candle close, without issuing a setup twice.
            await asyncio.sleep(5)

    async def scan(self, *, execute: bool = False) -> dict:
        if self._lock.locked():
            return await asyncio.to_thread(self.status)
        async with self._lock:
            owner = self.store.acquire(int(time.time()))
            if owner is None:
                return await asyncio.to_thread(self.status)
            try:
                async with asyncio.timeout(180):
                    await self._scan(execute=execute)
            except asyncio.TimeoutError:
                self.store.update_state(last_error="Scan timed out. An unconfirmed purchase is never retried.")
            except Exception as exc:
                self.store.update_state(last_error=f"Scan unavailable ({type(exc).__name__})")
            finally:
                self.store.release(owner)
        return await asyncio.to_thread(self.status)

    async def _scan(self, *, execute: bool = True):
        config = self.store.state()["config"]
        blockers: list[str] = []
        frames: dict[str, list[dict]] = {}
        async with self._market_lock:
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
                        blockers.append("One-minute Rise/Fall contracts are unavailable")
                    for name, step in TIMEFRAMES.items():
                        captured = int(time.time())
                        rows = await client.get_candles(SYMBOL, granularity=step, count=500)
                        frames[name] = captured_closed_candles(rows, step, captured)
            finally:
                await client.disconnect()
        now = int(time.time())
        # Recursive FRAMA/range/EMA state must start at the training origin.
        # A short live-only seed would otherwise change feature values.
        analysis = await asyncio.to_thread(self._analyze_frames, frames, now)
        technical_direction = analysis.get("direction", "NO TRADE")
        now = int(time.time())
        blockers.extend(analysis.get("blockers") or [])
        decision = int(analysis.get("decision_epoch") or 0)
        if not 0 <= now - decision <= MAX_ENTRY_AGE:
            blockers.append("Wait for the next 1-minute close: entry window is 20 seconds")
        analysis.update(
            technical_direction=technical_direction, blockers=list(dict.fromkeys(blockers)),
            eligible=not blockers and technical_direction in {"RISE", "FALL"},
            direction=technical_direction if not blockers else "NO TRADE",
        )
        self._confidence = confidence_view(analysis)
        self.store.update_state(analyses=[analysis], last_scan=now, last_error=None)
        if not execute or not analysis["eligible"] or not self.store.state().get("running"):
            return
        if settings.bot_mode != "synthetic":
            return
        if config["demo_auto_trade"] and not execution_status()["allowed"]:
            self.store.update_state(last_error=execution_status()["reason"])
            return
        signal = {
            "symbol": SYMBOL, "direction": technical_direction, "duration_seconds": DURATION_SECONDS,
            "created_epoch": now, "decision_epoch": decision, "expiry_epoch": now + DURATION_SECONDS,
            "setup_key": f"{SYMBOL}:{decision}", "stake": config["stake"],
            "observed_at": time.time(), "strategy_version": STRATEGY_VERSION,
            "features": analysis.get("features", {}), "checks": analysis.get("factors", []),
            "paper": {"source": "forward_tick_paper", "outcome": "pending"},
            "net_payout": None,
        }
        # Pricing is read-only and optional. Do not invent a payout when public
        # quotes are unavailable. USD is explicitly a paper reference currency.
        quote_client = DerivWSClient()
        try:
            async with asyncio.timeout(3):
                await quote_client.connect()
                response = await quote_client.send({
                    "proposal": 1, "amount": 1, "basis": "stake", "currency": "USD",
                    "contract_type": "CALL" if technical_direction == "RISE" else "PUT",
                    "duration": 1, "duration_unit": "m", "underlying_symbol": SYMBOL,
                })
                from app.synthetic.execution import _validate_proposal
                proposal = response.get("proposal")
                _, ask, payout = _validate_proposal(proposal, stake=1, now=time.time())
                signal.update(net_payout=payout / ask - 1, paper_quote={
                    "ask": ask, "payout": payout, "currency": "USD", "spot_time": proposal["spot_time"],
                    "source": "public_proposal", "break_even": ask / payout,
                })
        except Exception:
            pass
        finally:
            await quote_client.disconnect()
        now = int(time.time())
        if not 0 <= now - decision <= MAX_ENTRY_AGE:
            self.store.update_state(last_error="Pricing exceeded the entry window; wait for the next candle")
            return
        signal.update(observed_at=time.time(), created_epoch=now, expiry_epoch=now + DURATION_SECONDS)
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


def _vote(value) -> str | None:
    """A directional reading. Sideways, neutral and missing values do not vote."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value == 0:
            return None
        return "RISE" if value > 0 else "FALL"
    text = str(value).upper()
    return text if text in {"RISE", "FALL"} else None


def _signal_checks(analysis: dict) -> list[dict]:
    """The same checks a trade uses, each with its own direction."""
    indicators = analysis.get("indicators") if isinstance(analysis.get("indicators"), dict) else {}
    frames = analysis.get("timeframes") if isinstance(analysis.get("timeframes"), dict) else {}
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    rows = []
    for key, name, source in (
        ("frama", "FRAMA", (indicators.get("frama") or {}).get("direction") if isinstance(indicators.get("frama"), dict) else None),
        ("range_filter", "Range Filter", (indicators.get("range_filter") or {}).get("direction") if isinstance(indicators.get("range_filter"), dict) else None),
        ("m5", "5m trend", (frames.get("5m") or {}).get("trend") if isinstance(frames.get("5m"), dict) else None),
        ("m15", "15m trend", (frames.get("15m") or {}).get("trend") if isinstance(frames.get("15m"), dict) else None),
        ("momentum", "Momentum", features.get("momentum")),
    ):
        side = _vote(source)
        if side is None and source is None:
            continue
        rows.append({
            "id": key, "name": name, "side": side or "WAIT",
            "status": "PASS" if side else "WAIT",
            "detail": side or "No direction on the closed candle",
        })
    factors = analysis.get("factors") if isinstance(analysis.get("factors"), list) else []
    fresh = next((item for item in factors if isinstance(item, dict) and item.get("id") == "fresh_signal"), None)
    if isinstance(fresh, dict):
        passed = fresh.get("status") == "PASS"
        rows.append({
            "id": "fresh_signal", "name": "Recent signal", "side": "PASS" if passed else "BLOCK",
            "status": "PASS" if passed else "BLOCK",
            "detail": "FRAMA or Range Filter crossed within the last three closed candles" if passed else "The entry signal is older than three closed candles",
        })
    return rows


def confidence_view(analysis: dict, *, updated_at: int | None = -1) -> dict:
    """Signal agreement only. The trained model is not used; its probabilities had no trading value."""
    checks = _signal_checks(analysis)
    required = ("frama", "range_filter", "m5", "m15", "momentum")
    by_id = {item["id"]: item for item in checks}
    fresh = by_id.get("fresh_signal")
    fresh_ok = fresh is None or fresh["status"] == "PASS"
    if any(key in by_id for key in required):
        rise_votes = sum(by_id.get(key, {}).get("side") == "RISE" for key in required)
        fall_votes = sum(by_id.get(key, {}).get("side") == "FALL" for key in required)
        total = len(required)
        combined_rise = rise_votes / total
        combined_fall = fall_votes / total
        agreed_side = "RISE" if rise_votes == total else "FALL" if fall_votes == total else None
        waiting = [by_id[key]["name"] for key in required if key in by_id and by_id[key]["side"] not in {"RISE", "FALL"}]
        if agreed_side and fresh_ok:
            direction = agreed_side
            reason = f"FRAMA, Range Filter, 5m, 15m and momentum all say {agreed_side.title()}."
        elif agreed_side:
            direction = "NO TRADE"
            reason = f"The five signals say {agreed_side.title()}, but the entry cross is older than three closed candles."
        else:
            direction = "NO TRADE"
            missing = ", ".join(waiting) if waiting else "The signal directions"
            reason = f"Not a trade. {missing} must show the same Rise or Fall."
    else:
        combined_rise = combined_fall = None
        direction = "NO TRADE"
        reason = "Waiting for the closed-candle signals."
    combined_confidence = None if combined_rise is None or combined_fall is None else max(combined_rise, combined_fall)
    return {
        "symbol": SYMBOL,
        "direction": direction,
        "p_rise": None,
        "p_fall": None,
        "p_tie": None,
        "confidence": None,
        "probability_note": "Indicator agreement is not a win probability.",
        "combined_p_rise": combined_rise,
        "combined_p_fall": combined_fall,
        "combined_confidence": combined_confidence,
        "checks": checks,
        "threshold": None,
        "decision_epoch": analysis.get("decision_epoch"),
        "promoted": False,
        "reason": reason,
        "updated_at": int(time.time()) if updated_at == -1 else updated_at,
    }


synthetic_service = SyntheticService()
