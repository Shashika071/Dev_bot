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
from app.forex.analysis import analyze_forex, chart_overlays
from app.forex.context import evaluate_context, load_context
from app.forex.history import history_root, load_candles
from app.forex.jobs import forex_jobs
from app.forex.ml.predict import ml_blocks_entry, ml_status, predict_frames, score_idea
from app.forex.policy import TIMEFRAMES, session_context, supports_rise_fall, validate_config
from app.forex.store import ForexStore
from app.trade_prefs import load_trade_prefs, token_status

CHART_GRANULARITIES = {"15m": 900, "1h": 3600, "4h": 14400}
# First paint should already feel deep; Deriv caps each page, so we page backward.
CHART_DEFAULT_BARS = {"15m": 1200, "1h": 900, "4h": 600}
CHART_PAGE_SIZE = 500
CHART_MAX_PAGES = 6


def captured_closed_candles(rows: list[dict], step: int, captured_at: int) -> list[dict]:
    """Never let a saved partial candle mature into a purported closed candle."""
    result = []
    for row in rows:
        try:
            if int(row["epoch"]) + step <= captured_at:
                result.append(dict(row))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return result


async def capture_candles(client, symbol: str, name: str) -> list[dict]:
    # Use request start, a conservative lower bound on the provider's capture
    # time. Even a response arriving just after a boundary may contain a partial.
    captured_at = int(time.time())
    rows = await client.get_candles(symbol, granularity=TIMEFRAMES[name], count=240)
    return captured_closed_candles(rows, TIMEFRAMES[name], captured_at)


def _merge_candles(*batches: list[dict]) -> list[dict]:
    merged: dict[int, dict] = {}
    for batch in batches:
        for row in batch or []:
            try:
                epoch = int(row["epoch"])
                if epoch <= 0:
                    continue
                merged[epoch] = {
                    "epoch": epoch,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return [merged[epoch] for epoch in sorted(merged)]


def _aggregate_4h_from_1h(rows_1h: list[dict]) -> list[dict]:
    """Build 4h OHLC when Deriv's native 4h feed is thin or unavailable."""
    buckets: dict[int, dict] = {}
    for row in sorted(rows_1h, key=lambda item: int(item["epoch"])):
        epoch = int(row["epoch"])
        bucket = epoch - (epoch % 14400)
        bar = buckets.get(bucket)
        if bar is None:
            buckets[bucket] = {
                "epoch": bucket,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        else:
            bar["high"] = max(bar["high"], float(row["high"]))
            bar["low"] = min(bar["low"], float(row["low"]))
            bar["close"] = float(row["close"])
    return [buckets[epoch] for epoch in sorted(buckets)]


async def _paged_candles(
    client: DerivWSClient,
    symbol: str,
    *,
    granularity: int,
    target: int,
) -> list[dict]:
    stored: list[dict] = []
    end: int | str = "latest"
    oldest_seen: int | None = None
    for _ in range(CHART_MAX_PAGES):
        batch = await client.get_candles(
            symbol, granularity=granularity, count=min(CHART_PAGE_SIZE, target), end=end)
        if not batch:
            break
        stored = _merge_candles(stored, batch)
        first = int(batch[0]["epoch"])
        if oldest_seen is not None and first >= oldest_seen:
            break
        oldest_seen = first
        if len(stored) >= target:
            break
        end = first - 1
        await asyncio.sleep(0.2)
    return stored[-target:] if len(stored) > target else stored


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
        self._live_frames: dict[str, list] = {}
        self._history_cache: dict[str, list] | None = None
        self._ml_idea: dict | None = None

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
                "Public calendar + central-bank feeds load automatically when enabled. "
                "Or set FOREX_CONTEXT_FILE / FOREX_CONTEXT_URL / TRADING_ECONOMICS_API_KEY. "
                "Sample clock:server files cannot authorize trades.",
            )
        jobs = forex_jobs.snapshot()
        return {**state, "news": news, "execution": execution_status(), "daily_count": daily_count,
                "signals_remaining_today": remaining, "duration_hours": 4,
                "product": {"mode": "forex_rise_fall", "duration_seconds": 14400, "daily_cap": 2,
                            "timezone": "Asia/Colombo", "contract": "CALL_PUT_zero_barrier",
                            "hybrid": "checklist_plus_ml", "ml_symbol": "frxEURUSD"},
                "ml": ml_status(settings.model_dir),
                "ml_idea": self._ml_idea,
                "history": jobs["history"],
                "train": jobs["train"],
                "signals": self.store.signals()}

    def _history_tail(self) -> dict[str, list]:
        if self._history_cache is None:
            root = history_root(settings.model_dir)
            self._history_cache = {tf: load_candles(root, tf)[-400:] for tf in ("15m", "1h", "4h")}
        return self._history_cache

    def _refresh_idea(self, symbol: str, timeframe: str, candles: list[dict]) -> dict | None:
        """Rescore the display-only lean from closed bars. Does not place trades."""
        if symbol != "frxEURUSD":
            return None
        frames = {tf: list(rows) for tf, rows in self._history_tail().items()}
        if self._live_frames:
            for tf, rows in self._live_frames.items():
                if rows:
                    frames[tf] = _merge_candles(frames.get(tf) or [], rows)[-400:]
        if candles and timeframe in {"15m", "1h", "4h"}:
            frames[timeframe] = _merge_candles(frames.get(timeframe) or [], candles)[-400:]
        idea = score_idea(settings.model_dir, frames, now=int(time.time()))
        if idea.get("state") == "idea" or not (self._ml_idea and self._ml_idea.get("state") == "idea"):
            self._ml_idea = idea
        return self._ml_idea

    async def chart(self, symbol: str, timeframe: str = "15m", count: int | None = None) -> dict:
        symbol = str(symbol or "frxEURUSD")
        if not symbol.startswith("frx") or len(symbol) != 9:
            raise ValueError("Only Forex pairs are supported on the chart")
        if timeframe not in CHART_GRANULARITIES:
            raise ValueError("Chart timeframe must be 15m, 1h, or 4h")
        target = int(count) if count else CHART_DEFAULT_BARS[timeframe]
        target = max(80, min(target, 2500))
        granularity = CHART_GRANULARITIES[timeframe]
        source = "deriv"
        client = DerivWSClient()
        try:
            async with asyncio.timeout(75):
                await client.connect()
                try:
                    candles = await _paged_candles(
                        client, symbol, granularity=granularity, target=target)
                except Exception:
                    candles = []
                # Native 4h can be thin/quirky on public Deriv; synthesize from 1h.
                if timeframe == "4h" and len(candles) < 40:
                    hourly = await _paged_candles(
                        client, symbol, granularity=3600, target=min(target * 4, 2000))
                    synthesized = _aggregate_4h_from_1h(hourly)
                    if len(synthesized) > len(candles):
                        candles = synthesized[-target:]
                        source = "aggregated_1h"
                if not candles:
                    raise RuntimeError(f"No {timeframe} candles returned for {symbol}")
        finally:
            await client.disconnect()
        # Always derive overlays from the candles on screen so toggles work
        # without waiting for a scanner run. Prefer scan levels when present.
        overlays = chart_overlays(candles)
        state = self.store.state()
        analysis = next((row for row in state.get("analyses", []) if row.get("symbol") == symbol), None)
        scan_levels = (analysis or {}).get("levels") or {}
        levels = scan_levels if any(scan_levels.get(key) for key in (
            "support_resistance", "supply_demand", "liquidity")) or (
            isinstance(scan_levels.get("fibonacci"), dict) and scan_levels["fibonacci"].get("available")
        ) else overlays["levels"]
        events = (analysis or {}).get("events") or overlays["events"]
        signals = [row for row in self.store.signals() if row.get("symbol") == symbol][:40]
        last_epoch = int(candles[-1]["epoch"]) if candles else 0
        span_days = round((last_epoch - int(candles[0]["epoch"])) / 86400, 2) if len(candles) > 1 else 0.0
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "granularity": granularity,
            "candles": candles,
            "levels": levels,
            "events": events,
            "analysis": analysis,
            "signals": signals,
            "ml": (analysis or {}).get("ml") or ml_status(settings.model_dir),
            "ml_idea": self._refresh_idea(symbol, timeframe, candles),
            "server_time": int(time.time()),
            "last_candle_epoch": last_epoch,
            "last_candle_age_seconds": max(0, int(time.time()) - last_epoch - granularity) if last_epoch else None,
            "source": source,
            "span_days": span_days,
            "bar_count": len(candles),
        }

    async def symbols(self) -> dict:
        client = DerivWSClient()
        try:
            async with asyncio.timeout(25):
                await client.connect()
                response = await client.get_active_symbols()
                return {"symbols": forex_symbols(response.get("active_symbols", []))}
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            return {"symbols": [], "error": f"Deriv symbol discovery unavailable ({detail})"}
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
                detail = str(exc).strip() or type(exc).__name__
                self.store.update_state(last_error=f"Scan unavailable ({detail})")
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
                            values = await asyncio.gather(*(capture_candles(client, symbol, name) for name in group))
                            frames.update(zip(group, values))
                    except Exception as exc:
                        pair_blockers.append(f"Market history/contract lookup failed ({type(exc).__name__})")
                histories[symbol] = frames
                if symbol == "frxEURUSD" and frames.get("15m"):
                    self._live_frames = {
                        tf: list(frames.get(tf) or [])[-400:]
                        for tf in ("15m", "1h", "4h")
                    }
                # Recompute time after network requests, not at the start of a slow scan.
                checked_at = int(time.time())
                analysis = analyze_forex(frames, symbol=symbol, now=checked_at, duration_seconds=duration)
                news = evaluate_context(context, symbol, checked_at, duration)
                sessions = session_context(checked_at, duration)
                analysis["technical_direction"] = analysis["direction"]
                # Hybrid ML filter: only for EURUSD when a model is trained.
                ml_view = {"ready": False, "hybrid_passed": None, "reason": "Rules-only (ML trains on EURUSD)"}
                if symbol == "frxEURUSD" and analysis.get("direction") in {"RISE", "FALL"}:
                    ml_view = predict_frames(
                        settings.model_dir, frames, now=checked_at,
                        checklist_direction=analysis["direction"])
                    analysis["ml"] = ml_view
                    if ml_blocks_entry(ml_view):
                        if ml_view.get("state") in {"unavailable", "invalid"}:
                            pair_blockers.append(
                                f"Installed ML filter unavailable ({ml_view.get('reason')})")
                        else:
                            pair_blockers.append(
                                f"ML hybrid filter disagrees or is unsure "
                                f"(model={ml_view.get('direction')}, p={ml_view.get('probability')})")
                    elif not ml_view.get("installed"):
                        analysis["ml_note"] = ml_view.get("reason")
                else:
                    analysis["ml"] = ml_view
                if symbol == "frxEURUSD":
                    analysis["ml_idea"] = score_idea(settings.model_dir, frames, now=checked_at)
                    self._ml_idea = analysis["ml_idea"]
                analysis["blockers"] += pair_blockers + news["blockers"] + sessions["blockers"]
                analysis["factors"] += news["factors"] + sessions["factors"]
                analysis["news"] = news
                analysis["ready"] = analysis["ready"] and not analysis["blockers"]
                analysis["hybrid_passed"] = bool(analysis["ready"])
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
        # Prefer the richest per-pair news payload for the desk UI.
        desk = {}
        for analysis in analyses:
            candidate_news = analysis.get("news") or {}
            if candidate_news.get("sources"):
                desk = candidate_news
                break
        if not desk and analyses:
            desk = analyses[0].get("news") or {}
        self.store.update_state(
            analyses=analyses,
            last_scan_at=datetime.now(timezone.utc).isoformat(),
            last_error=None,
            news={
                "ready": news_ready,
                "reason": news_reason,
                "source": context.get("source") or desk.get("source"),
                "sources": desk.get("sources") or [],
                "events": desk.get("events") or [],
                "headlines": desk.get("headlines") or [],
                "attribution": desk.get("attribution") or context.get("attribution"),
                "public": bool(context.get("public") or desk.get("public")),
                "as_of": desk.get("as_of") or context.get("as_of"),
                "age_seconds": desk.get("age_seconds"),
            },
        )
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
        if candidate["symbol"] == "frxEURUSD":
            ml_final = predict_frames(
                settings.model_dir, histories[candidate["symbol"]], now=emitted,
                checklist_direction=final["direction"])
            if ml_blocks_entry(ml_final):
                self.store.update_state(last_error=f"ML hybrid filter blocked emission ({ml_final.get('reason') or ml_final.get('state')})")
                return
            final["ml"] = ml_final
        signal = {"symbol": candidate["symbol"], "direction": final["direction"], "score": final["score"],
                  "duration_seconds": duration, "created_epoch": emitted, "created_at": datetime.fromtimestamp(emitted, timezone.utc).isoformat(),
                  "expiry_epoch": emitted + duration, "stake": config["stake"], "reasons": candidate["reasons"],
                  "ml": final.get("ml"), "analysis": candidate,
                  "setup_key": f"{candidate['symbol']}:{final['direction']}:{final['as_of_epoch']}:{duration}"}
        loss_limit = float(load_trade_prefs().get("daily_loss_limit") or 0)
        reserved, reason = self.store.reserve(signal, emitted, expected_config=config,
                                             stake_budget=loss_limit if loss_limit > 0 else None)
        if not reserved:
            self.store.update_state(last_error=reason)
            return
        if config["demo_auto_trade"]:
            from app.forex.execution import execute_demo

            def can_execute():
                state = self.store.state()
                if not (state.get("running") is True and state["config"] == config and settings.bot_mode == "forex"):
                    return False
                buy_time = int(time.time())
                current_analysis = analyze_forex(histories[candidate["symbol"]], symbol=candidate["symbol"],
                                                now=buy_time, duration_seconds=duration)
                if not current_analysis["ready"] or current_analysis["direction"] != reserved["direction"]:
                    return False
                if not evaluate_context(context, candidate["symbol"], buy_time, duration)["ready"]:
                    return False
                if session_context(buy_time, duration)["blockers"]:
                    return False
                if candidate["symbol"] == "frxEURUSD":
                    ml_current = predict_frames(settings.model_dir, histories[candidate["symbol"]],
                                                now=buy_time, checklist_direction=reserved["direction"])
                    if ml_blocks_entry(ml_current):
                        return False
                return True

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
