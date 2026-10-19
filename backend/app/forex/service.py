"""Bounded scans, durable ranking/quotas and optional demo execution."""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.deriv.client import DerivWSClient
from app.execution.gates import server_execution_allowed
from app.forex.analysis import analyze_forex
from app.forex.context import evaluate_context, load_context
from app.forex.policy import TIMEFRAMES, session_context, supports_rise_fall, validate_config
from app.forex.store import ForexStore
from app.trade_prefs import token_status


def execution_status() -> dict:
    token = bool(token_status().get("token_configured"))
    enabled = server_execution_allowed()
    allowed = token and enabled and settings.bot_mode == "forex"
    return {"allowed": allowed, "server_allowed": enabled, "token_configured": token,
            "reason": "Demo account will be verified before every purchase" if allowed else
                      "Enable Forex mode" if settings.bot_mode != "forex" else
                      "Save a Deriv token in the demo account settings" if not token else "Enable demo execution in account settings"}


def forex_symbols(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        symbol = row.get("symbol") or row.get("underlying_symbol") or ""
        if row.get("market") != "forex" or len(symbol) != 9 or not symbol.startswith("frx") or not symbol[3:].isalpha():
            continue
        result.append({"symbol": symbol, "display_name": row.get("display_name") or row.get("underlying_symbol_name") or symbol[3:6] + "/" + symbol[6:],
                       "is_open": row.get("exchange_is_open") in (True, 1, "1") and row.get("is_trading_suspended", 0) in (False, 0, "0")})
    return sorted(result, key=lambda row: row["symbol"])


def currency_strength(histories: dict, now: int) -> dict:
    contributions: dict[str, list[float]] = {}
    for symbol, frames in histories.items():
        rows = [r for r in frames.get("4h", []) if int(r["epoch"]) + 14400 <= now]
        if len(rows) < 7 or now - int(rows[-1]["epoch"]) > 28800:
            continue
        if float(rows[-7]["close"]) <= 0 or float(rows[-1]["close"]) <= 0:
            continue
        change = math.log(float(rows[-1]["close"]) / float(rows[-7]["close"])) * 100
        if not math.isfinite(change):
            continue
        contributions.setdefault(symbol[3:6], []).append(change)
        contributions.setdefault(symbol[6:9], []).append(-change)
    return {currency: {"value": round(sum(values) / len(values), 4), "pairs": len(values)}
            for currency, values in contributions.items()}


class ForexService:
    def __init__(self, store: ForexStore | None = None):
        self._store = store
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def store(self) -> ForexStore:
        if self._store is None:
            self._store = ForexStore(str(Path(settings.model_dir) / "forex.sqlite"))
        return self._store

    def status(self) -> dict:
        state = self.store.state()
        state.setdefault("analyses", [])
        state.setdefault("news", {"ready": False, "reason": "Run a scan to verify the configured calendar feed"})
        config = state.get("config") or {}
        now = int(time.time())
        daily_count = self.store.daily_count(now)
        cap = int(config.get("max_signals_per_day", 2))
        remaining = max(0, cap - daily_count)
        news = dict(state.get("news") or {})
        if not news.get("ready"):
            news.setdefault(
                "setup_hint",
                "Set FOREX_CONTEXT_FILE to data/forex/context.sample.json (or a live feed / "
                "TRADING_ECONOMICS_API_KEY), then Scan now. See docs/forex-rise-fall.md.",
            )
        return {**state, "news": news, "execution": execution_status(), "daily_count": daily_count,
                "signals_remaining_today": remaining, "duration_hours": 4,
                "product": {"mode": "forex_rise_fall", "duration_seconds": 14400, "daily_cap": 2,
                            "timezone": "Asia/Colombo", "contract": "CALL_PUT_zero_barrier"},
                "signals": self.store.signals()}

    async def symbols(self) -> dict:
        client = DerivWSClient()
        try:
            async with asyncio.timeout(25):
                await client.connect()
                response = await client.get_active_symbols()
                return {"symbols": forex_symbols(response.get("active_symbols", []))}
        except Exception as exc:
            return {"symbols": [], "error": f"Deriv symbol discovery unavailable ({type(exc).__name__})"}
        finally:
            await client.disconnect()

    async def resume(self):
        if settings.bot_mode == "forex" and self.store.state().get("running"):
            self._ensure_loop()

    def _ensure_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def start(self, updates: dict):
        if settings.bot_mode != "forex":
            raise ValueError("Set BOT_MODE=forex before starting Forex scans")
        config = validate_config(updates, self.store.state().get("config"))
        self.store.update_state(config=config, running=True, last_error=None)
        self._ensure_loop()
        return self.status()

    async def stop(self):
        # Do not cancel an in-flight buy: let it record the broker's response.
        self.store.update_state(running=False)
        return self.status()

    async def shutdown(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while self.store.state().get("running"):
            try:
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.update_state(last_error=f"Scan failed ({type(exc).__name__})")
            await asyncio.sleep(300)

    async def scan(self) -> dict:
        if self._lock.locked():
            return self.status()
        async with self._lock:
            owner = self.store.acquire(int(time.time()))
            if owner is None:
                return self.status()
            try:
                # Shorter than lease: another process must never overlap this scan.
                async with asyncio.timeout(180):
                    await self._scan()
            except asyncio.TimeoutError:
                self.store.update_state(last_error="Scan timed out; no purchase is retried. Review unresolved attempts before restarting.")
            except Exception as exc:
                self.store.update_state(last_error=f"Scan unavailable ({type(exc).__name__})")
            finally:
                self.store.release(owner)
        return self.status()

    async def _scan(self):
        config = self.store.state()["config"]
        duration = config["duration_seconds"]
        now = int(time.time())
        client = DerivWSClient()
        histories = {}
        analyses = []
        context = await load_context(settings, now, duration)
        try:
            await client.connect()
            listing = await client.get_active_symbols()
            available = {row["symbol"]: row for row in forex_symbols(listing.get("active_symbols", []))}
            for symbol in config["symbols"]:
                pair_blockers = []
                frames = {}
                market = available.get(symbol)
                if not market or not market["is_open"]:
                    pair_blockers.append("Forex pair is unavailable, suspended, or its exchange is closed")
                else:
                    try:
                        contracts = (await client.get_contracts_for(symbol)).get("contracts_for", {}).get("available", [])
                        if not supports_rise_fall(contracts, duration):
                            pair_blockers.append("No zero-barrier CALL and PUT contracts support the requested duration")
                        # Two requests at a time keeps the broker load bounded.
                        for group in [("1m", "5m"), ("15m", "1h"), ("4h", "1d")]:
                            values = await asyncio.gather(*(client.get_candles(symbol, granularity=TIMEFRAMES[name], count=240) for name in group))
                            frames.update(zip(group, values))
                    except Exception as exc:
                        pair_blockers.append(f"Market history/contract lookup failed ({type(exc).__name__})")
                histories[symbol] = frames
                # Recompute time after network requests, not at the start of a slow scan.
                checked_at = int(time.time())
                analysis = analyze_forex(frames, symbol=symbol, now=checked_at, duration_seconds=duration)
                news = evaluate_context(context, symbol, checked_at, duration)
                sessions = session_context(checked_at, duration)
                analysis["technical_direction"] = analysis["direction"]
                analysis["blockers"] += pair_blockers + news["blockers"] + sessions["blockers"]
                analysis["factors"] += news["factors"] + sessions["factors"]
                analysis["news"] = news
                analysis["ready"] = analysis["ready"] and not analysis["blockers"]
                if not analysis["ready"]:
                    analysis["direction"] = "WAIT"
                analyses.append(analysis)
        finally:
            await client.disconnect()
        strength = currency_strength(histories, int(time.time()))
        for analysis in analyses:
            for factor in analysis["factors"]:
                if factor["id"] == 29 and len(histories) > 1:
                    base, quote = analysis["symbol"][3:6], analysis["symbol"][6:9]
                    if base in strength and quote in strength:
                        factor.update(status="proxy", detail=f"Basket mean 24h log returns: {base} {strength[base]['value']:+.3f}%, {quote} {strength[quote]['value']:+.3f}%. Watchlist-dependent proxy; not an independent signal.")
            analysis["factors"].sort(key=lambda item: item["id"])
        news_ready = any(a["news"]["ready"] for a in analyses)
        news_reason = "Calendar coverage ready for at least one pair" if news_ready else "; ".join(dict.fromkeys(a["news"]["reason"] for a in analyses))
        self.store.update_state(analyses=analyses, last_scan_at=datetime.now(timezone.utc).isoformat(),
                                last_error=None, news={"ready": news_ready, "reason": news_reason, "source": context.get("source")})
        candidates = sorted((a for a in analyses if a["ready"]), key=lambda a: a["score"], reverse=True)
        current = self.store.state()
        if not current.get("running") or current["config"] != config or not candidates:
            return
        if config["demo_auto_trade"] and not execution_status()["allowed"]:
            self.store.update_state(last_error=execution_status()["reason"])
            return
        candidate = candidates[0]
        emitted = int(time.time())
        # Re-evaluate freshness/news immediately before reserving the strongest setup.
        final = analyze_forex(histories[candidate["symbol"]], symbol=candidate["symbol"], now=emitted, duration_seconds=duration)
        final_news = evaluate_context(context, candidate["symbol"], emitted, duration)
        if not final["ready"] or not final_news["ready"] or session_context(emitted, duration)["blockers"]:
            return
        bars = [r for r in histories[candidate["symbol"]]["15m"] if int(r["epoch"]) + 900 <= emitted]
        signal = {"symbol": candidate["symbol"], "direction": final["direction"], "score": final["score"],
                  "duration_seconds": duration, "created_epoch": emitted, "created_at": datetime.fromtimestamp(emitted, timezone.utc).isoformat(),
                  "expiry_epoch": emitted + duration, "stake": config["stake"], "reasons": candidate["reasons"],
                  "analysis": candidate, "setup_key": f"{candidate['symbol']}:{final['direction']}:{bars[-1]['epoch']}:{duration}"}
        reserved, reason = self.store.reserve(signal, emitted)
        if not reserved:
            self.store.update_state(last_error=reason)
            return
        if config["demo_auto_trade"]:
            from app.forex.execution import execute_demo

            def can_execute():
                state = self.store.state()
                return state.get("running") is True and state["config"] == config and settings.bot_mode == "forex"

            try:
                result = await execute_demo({**reserved, "_can_execute": can_execute}, stake=config["stake"])
            except asyncio.CancelledError:
                self.store.finish(reserved["id"], {"ok": False, "status": "unknown", "reason": "Execution interrupted; reconcile with Deriv, never retry automatically"})
                raise
            except Exception as exc:
                result = {"ok": False, "status": "unknown", "reason": f"Execution interrupted ({type(exc).__name__}); reconcile with Deriv"}
            self.store.finish(reserved["id"], result)
            if result.get("status") == "unknown":
                self.store.update_state(running=False, last_error="Purchase outcome unknown. Verify the account contract history before any further execution.")


forex_service = ForexService()
