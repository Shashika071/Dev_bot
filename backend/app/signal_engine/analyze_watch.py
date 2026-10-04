"""
Server-side Analyze / Force watcher.

Survives browser tab close / re-login. One active watch at a time.
Persists status under MODEL_DIR so UI can reconnect after refresh.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional

import structlog

from app.config import settings
from app.database import async_session
from app.deriv.auto_trade import maybe_auto_trade_after_signal
from app.notifications.browser import send_signal_notification
from app.signal_engine.manual_generate import generate_manual_signal

logger = structlog.get_logger(__name__)

POLL_SECONDS = 1.0


def _state_path() -> str:
    return os.path.join(settings.model_dir, "analyze_watch_state.json")


def _default_state() -> dict[str, Any]:
    return {
        "running": False,
        "want_running": False,
        "mode": "standard",
        "min_probability": None,
        "attempt": 0,
        "started_at": None,
        "updated_at": None,
        "stopped_at": None,
        "stop_reason": None,
        "ok": False,
        "message": "",
        "reason": "",
        "analysis": [],
        "signal": None,
        "trade": None,
        "signals_this_session": 0,
        "trades_ok_this_session": 0,
        "last_error": None,
    }


class AnalyzeWatchService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._state = _default_state()
        self._load_disk()

    def _load_disk(self) -> None:
        path = _state_path()
        try:
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    st = _default_state()
                    st.update({k: saved[k] for k in saved if k in st})
                    # Task dies with process — mark not running until resume
                    st["running"] = False
                    self._state = st
        except Exception as e:
            logger.warning("analyze_watch_load_failed", error=str(e))

    def _save_disk(self) -> None:
        try:
            os.makedirs(settings.model_dir, exist_ok=True)
            with open(_state_path(), "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, default=str)
        except Exception as e:
            logger.warning("analyze_watch_save_failed", error=str(e))

    def status(self) -> dict[str, Any]:
        return dict(self._state)

    def _fatal_reason(self, reason: str) -> bool:
        r = (reason or "").lower()
        if not r:
            return False
        if "cooldown" in r:
            return False
        if "train first" in r or "no compatible trained" in r or "use cross-barrier" in r:
            return True
        if "no trained model for model barrier" in r:
            return True
        if "confirm contract settings" in r:
            return True
        if "max signals" in r or "daily cap" in r or "signals today" in r or "per day" in r:
            return True
        if "need more ticks" in r:
            return True
        return False

    async def start(
        self,
        *,
        mode: str = "standard",
        min_probability: Optional[float] = None,
    ) -> dict[str, Any]:
        mode = str(mode or "standard").strip().lower()
        if mode not in ("standard", "force_model_candles", "cross_barrier"):
            mode = "standard"
        mode_label = {
            "force_model_candles": "Force",
            "cross_barrier": "Cross-barrier",
        }.get(mode, "Analyze")

        async with self._lock:
            if self._task and not self._task.done():
                # Switch mode: stop current then restart
                self._stop.set()
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), timeout=3.0)
                except Exception:
                    self._task.cancel()
                    try:
                        await self._task
                    except Exception:
                        pass

            self._stop = asyncio.Event()
            now = time.time()
            self._state = {
                **_default_state(),
                "running": True,
                "want_running": True,
                "mode": mode,
                "min_probability": min_probability,
                "attempt": 0,
                "started_at": now,
                "updated_at": now,
                "message": f"Watching ({mode_label})…",
            }
            self._save_disk()
            self._task = asyncio.create_task(self._loop(), name="analyze-watch")
            logger.info("analyze_watch_started", mode=mode, min_probability=min_probability)
            return self.status()

    async def stop(self, reason: str = "stopped_by_user") -> dict[str, Any]:
        async with self._lock:
            self._stop.set()
            if self._task and not self._task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
                except Exception:
                    self._task.cancel()
                    try:
                        await self._task
                    except Exception:
                        pass
            self._task = None
            self._state["running"] = False
            self._state["want_running"] = False
            self._state["stopped_at"] = time.time()
            self._state["stop_reason"] = reason
            self._state["updated_at"] = time.time()
            if not self._state.get("ok"):
                self._state["message"] = "Watching stopped."
            self._save_disk()
            logger.info("analyze_watch_stopped", reason=reason)
            return self.status()

    async def resume_if_needed(self) -> None:
        """Call on API startup — restart watch if disk said it was intended to run."""
        path = _state_path()
        try:
            if not os.path.isfile(path):
                return
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            # Resume only if last stop wasn't user and last message was watching
            # We store want_running flag
            if isinstance(saved, dict) and saved.get("want_running"):
                mode = saved.get("mode") or "standard"
                min_p = saved.get("min_probability")
                await self.start(mode=mode, min_probability=min_p)
        except Exception as e:
            logger.warning("analyze_watch_resume_failed", error=str(e))

    async def _loop(self) -> None:
        mode = self._state.get("mode") or "standard"
        min_probability = self._state.get("min_probability")
        label = {
            "force_model_candles": "Force",
            "cross_barrier": "Cross-barrier",
        }.get(mode, "Analyze")
        self._state["want_running"] = True
        self._save_disk()

        try:
            while not self._stop.is_set():
                self._state["attempt"] = int(self._state.get("attempt") or 0) + 1
                attempt = self._state["attempt"]
                self._state["message"] = f"Watching ({label})… check #{attempt}"
                self._state["updated_at"] = time.time()
                self._save_disk()

                try:
                    async with async_session() as session:
                        result = await generate_manual_signal(
                            session,
                            mode=mode,
                            min_probability=(
                                float(min_probability) if min_probability is not None else None
                            ),
                        )
                        if result.get("ok") and result.get("signal"):
                            try:
                                await send_signal_notification(result["signal"])
                            except Exception:
                                pass
                            trade = None
                            try:
                                trade = await maybe_auto_trade_after_signal(
                                    session, result["signal"]
                                )
                            except Exception as e:
                                trade = {"ok": False, "error": str(e)}

                            sig = result["signal"]
                            p = float(sig.get("calibrated_probability") or 0) * 100
                            n_sig = int(self._state.get("signals_this_session") or 0) + 1
                            n_tr = int(self._state.get("trades_ok_this_session") or 0)
                            if trade and trade.get("ok"):
                                n_tr += 1

                            # Keep watching: cooldown = market rest before next;
                            # stop only at daily cap (e.g. 3/day) or user Stop.
                            from app.ops_prefs import load_ops_prefs
                            from app.signal_engine.daily_cap import DailyCapManager

                            ops = load_ops_prefs()
                            max_day = int(ops.get("max_signals_per_day", 3))
                            cool = int(ops.get("signal_cooldown_seconds", 540))
                            day_count = await DailyCapManager().get_signals_today(
                                session, str(sig.get("symbol") or "")
                            )

                            trade_bit = ""
                            if trade and not trade.get("skipped"):
                                if trade.get("ok"):
                                    trade_bit = f" · trade OK {trade.get('contract_id')}"
                                else:
                                    trade_bit = f" · trade fail: {trade.get('error')}"

                            done_for_day = day_count >= max_day
                            msg = (
                                f"{label} #{n_sig}: "
                                f"{str(sig.get('direction') or '').upper()} · "
                                f"{p:.1f}% · {sig.get('signal_id')}{trade_bit}. "
                                f"Day {day_count}/{max_day}."
                            )
                            if done_for_day:
                                msg += " Daily cap reached — watch stopped."
                            else:
                                msg += (
                                    f" Market rest {cool}s cooldown, then searching next…"
                                )

                            self._state.update(
                                {
                                    "running": not done_for_day,
                                    "want_running": not done_for_day,
                                    "ok": True,
                                    "signal": sig,
                                    "trade": trade,
                                    "analysis": result.get("analysis") or [],
                                    "reason": "",
                                    "message": msg,
                                    "signals_this_session": n_sig,
                                    "trades_ok_this_session": n_tr,
                                    "stopped_at": time.time() if done_for_day else None,
                                    "stop_reason": "daily_cap" if done_for_day else None,
                                    "updated_at": time.time(),
                                    "last_error": None,
                                }
                            )
                            self._save_disk()
                            logger.info(
                                "analyze_watch_signal",
                                mode=mode,
                                signal_id=sig.get("signal_id"),
                                attempt=attempt,
                                day_count=day_count,
                                continue_watch=not done_for_day,
                            )
                            if done_for_day:
                                return
                            # Continue loop — cooldown blocks until market rest ends
                            continue

                        reason = result.get("reason") or "No setup yet."
                        cool_wait = "cooldown" in reason.lower()
                        self._state.update(
                            {
                                "ok": False,
                                "reason": reason,
                                "analysis": result.get("analysis") or [],
                                "message": (
                                    f"Watching ({label})… #{attempt}: "
                                    + (
                                        f"Market rest — {reason}"
                                        if cool_wait
                                        else reason
                                    )
                                ),
                                "updated_at": time.time(),
                                "last_error": None,
                            }
                        )
                        self._save_disk()
                        if self._fatal_reason(reason):
                            self._state["running"] = False
                            self._state["want_running"] = False
                            self._state["stop_reason"] = "fatal"
                            self._state["stopped_at"] = time.time()
                            self._save_disk()
                            return
                except Exception as e:
                    logger.warning("analyze_watch_tick_error", error=str(e), attempt=attempt)
                    self._state["last_error"] = str(e)
                    self._state["message"] = (
                        f"Watching ({label})… #{attempt} error: {e} — retrying…"
                    )
                    self._state["updated_at"] = time.time()
                    self._save_disk()

                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
                    break
                except asyncio.TimeoutError:
                    continue
        finally:
            if self._state.get("running"):
                self._state["running"] = False
                self._state["want_running"] = False
                self._state["updated_at"] = time.time()
                if not self._state.get("stop_reason"):
                    self._state["stop_reason"] = "stopped"
                self._save_disk()


analyze_watch = AnalyzeWatchService()
