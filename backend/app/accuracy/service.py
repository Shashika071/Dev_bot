"""Public-tick signal service; never imports the trade or execution engines."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from .core import (
    BARRIER, COLOMBO_OFFSET, DELAY, DURATION, SYMBOL, SignalPolicy,
    market_trend_bias, touch_labels, validate_ticks,
)
from .training import load_artifact

HISTORY_PAGE_COUNT = 5000
MAX_HISTORY_PAGES = 64
HISTORY_TIMEOUT_SECONDS = 120.0
HISTORY_PAGE_TIMEOUT_SECONDS = 15.0
MAX_HISTORY_RECOVERY_ATTEMPTS = 3
HISTORY_RECOVERY_BACKOFF_SECONDS = 5.0
DEFAULT_COOLDOWN_SECONDS = 5760
DEFAULT_MAX_PER_DAY = 15


def _accuracy_prefs() -> tuple[int, int]:
    cool, cap = DEFAULT_COOLDOWN_SECONDS, DEFAULT_MAX_PER_DAY
    try:
        from app.trade_prefs import load_trade_prefs

        prefs = load_trade_prefs()
        cool = int(prefs.get("accuracy_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
        cap = int(prefs.get("accuracy_max_per_day", DEFAULT_MAX_PER_DAY))
    except Exception:
        pass
    return max(600, min(7200, cool)), max(1, min(15, cap))


def _accuracy_cooldown_seconds() -> int:
    return _accuracy_prefs()[0]


def _accuracy_max_per_day() -> int:
    return _accuracy_prefs()[1]


class AccuracyService:
    def __init__(self, output: Path):
        self.output=output
        self.task=None
        self.client=None
        self.bundle=None
        self.policy=None
        self.pending={}
        self.recent=deque(maxlen=100)
        self.ticks=deque(maxlen=4000)
        self.history_seconds=900
        self.loading_history=False
        self._backfill_requested=asyncio.Event()
        self._gap_epoch=None
        self._gap_generation=0
        self.last_evaluated=-1
        self.last_tick=None
        self.reason="Stopped"
        self.error=None
        self.lock=asyncio.Lock()

    def _log(self, record):
        self.output.mkdir(parents=True,exist_ok=True)
        with (self.output/"signals.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(record,allow_nan=False)+"\n")

    def _restore(self):
        self.pending={}; self.recent.clear()
        events=self.output/"signals.jsonl"
        if not events.exists():
            return
        for line in events.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event=json.loads(line)
            if event["type"]=="signal":
                self.policy.record(event["decision_epoch"])
                self.pending[event["signal_id"]]=event
                self.recent.append(event)
            elif event["type"]=="outcome":
                self.pending.pop(event["signal_id"],None)
                for signal in self.recent:
                    if signal["signal_id"]==event["signal_id"]:
                        signal["outcome"]=event["touched"]
                        signal["resolved"]=True
                        signal["resolved_epoch"]=event.get("resolved_epoch")

    def status(self):
        report=self.bundle["report"] if self.bundle else None
        if report is None:
            try:
                latest=json.loads((self.output/"latest.json").read_text(encoding="utf-8"))
                run=str(latest["run_id"])
                if Path(run).name==run and "/" not in run and "\\" not in run:
                    report=json.loads((self.output/run/"report.json").read_text(encoding="utf-8"))
            except (FileNotFoundError,KeyError,ValueError):
                pass
        cool, cap = _accuracy_prefs()
        if report is not None and self.policy is None:
            self.policy=SignalPolicy(
                min_probability=report["selection"]["threshold"],
                cooldown_seconds=cool,
                max_per_day=cap,
            )
            try:
                self._restore()
            except (ValueError,KeyError) as exc:
                self.error=f"Signal history needs repair: {exc}"
        elif self.policy is not None:
            if int(self.policy.cooldown_seconds) != cool:
                self.policy.cooldown_seconds = cool
            if int(self.policy.max_per_day) != cap:
                self.policy.max_per_day = cap
        today=(int(time.time())+COLOMBO_OFFSET)//86400
        feed_age=time.time()-self.last_tick["epoch"] if self.last_tick else None
        groups=(report or {}).get("feature_groups") or []
        groups=list(groups) if isinstance(groups,(list,tuple,dict)) else []
        auto_trade = {"accuracy_enabled": False, "allowed": False, "reason": "unknown"}
        trend_filter = True
        try:
            from app.execution.gates import permission_status
            from app.trade_prefs import load_trade_prefs

            prefs = load_trade_prefs()
            trend_filter = bool(prefs.get("accuracy_trend_filter_enabled", True))
            status = permission_status(prefs)
            auto_trade = {
                "accuracy_enabled": bool(prefs.get("accuracy_auto_trade_enabled")),
                "setup_enabled": bool(prefs.get("auto_trade_enabled")),
                "allowed": bool(status.get("allowed")) and bool(prefs.get("accuracy_auto_trade_enabled")),
                "reason": (
                    "accuracy_auto_trade_disabled"
                    if not prefs.get("accuracy_auto_trade_enabled")
                    else status.get("reason")
                ),
            }
        except Exception:
            pass
        return {"running":bool(self.task and not self.task.done()),"ready":report is not None,
                "reason":self.reason,"error":self.error,"last_tick":self.last_tick,
                "feed_age_seconds":feed_age,"feed_fresh":feed_age is not None and abs(feed_age)<=5,
                "signals_today":self.policy.counts.get(today,0) if self.policy else 0,
                "max_per_day":cap,"cooldown_seconds":cool,"timezone":"Asia/Colombo",
                "trend_filter_enabled":trend_filter,
                "feature_profile":(report or {}).get("feature_profile","compact_v1"),
                "feature_groups":groups,"history_loading":self.loading_history,
                "history_seconds":self.history_seconds,"history_ticks":len(self.ticks),
                "report":report,"signals":list(reversed(self.recent)),"mode":"accuracy_signals",
                "auto_trade":auto_trade}

    def apply_cooldown(self, seconds: int) -> None:
        """Apply UI cooldown to the live policy without restarting the watcher."""
        cool = max(600, min(7200, int(seconds)))
        if self.policy is not None:
            self.policy.cooldown_seconds = cool

    def apply_max_per_day(self, cap: int) -> None:
        """Apply UI daily signal cap to the live policy without restarting."""
        value = max(1, min(15, int(cap)))
        if self.policy is not None:
            self.policy.max_per_day = value

    async def reset_signals(self):
        """Delete forward-signal history and reset daily/cooldown slots."""
        async with self.lock:
            events = self.output / "signals.jsonl"
            cleared = 0
            if events.exists():
                try:
                    cleared = sum(1 for line in events.read_text(encoding="utf-8").splitlines() if line.strip())
                except OSError:
                    cleared = 0
                events.unlink(missing_ok=True)
            self.pending = {}
            self.recent.clear()
            if self.policy is not None:
                self.policy.reset_slots()
            self.reason = "Forward signals reset — slots cleared"
            self.error = None
            return {
                **self.status(),
                "reset": True,
                "cleared_events": cleared,
                "message": f"Cleared {cleared} history events; daily cap and cooldown reset.",
            }

    async def start(self):
        async with self.lock:
            if self.task and not self.task.done():
                return self.status()
            self.bundle=load_artifact(self.output)
            cool, cap = _accuracy_prefs()
            self.policy=SignalPolicy(
                min_probability=self.bundle["threshold"],
                cooldown_seconds=cool,
                max_per_day=cap,
            )
            self._restore()
            self.history_seconds=int(getattr(self.bundle["predictor"],"history_seconds",900))
            if not 900 <= self.history_seconds <= 86400:
                raise ValueError("Unsupported feature-history duration")
            capacity=4000 if self.history_seconds==900 else min(100000,max(30000,self.history_seconds+7200))
            self.ticks=deque(maxlen=capacity); self.last_evaluated=-1
            self.last_tick=None
            self.loading_history=False
            self._backfill_requested.clear(); self._gap_epoch=None; self._gap_generation=0
            self.reason="Connecting to public R_100 ticks"; self.error=None
            self.task=asyncio.create_task(self._run())
            return self.status()

    async def _load_history(self,since_epoch=None):
        """Page backwards while the subscription buffers new live ticks."""
        margin=120 if self.history_seconds==900 else 3600
        required=self.history_seconds+margin
        deadline=time.monotonic()+HISTORY_TIMEOUT_SECONDS
        history={}
        end="latest"
        previous_oldest=None
        for page in range(MAX_HISTORY_PAGES):
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise ValueError("Timed out loading the model's required tick history")
            self.reason=f"Loading model history: page {page+1} ({self.history_seconds//60} minutes required)"
            try:
                response=await asyncio.wait_for(
                    self.client.get_ticks_history(SYMBOL,count=HISTORY_PAGE_COUNT,end=end),
                    timeout=min(HISTORY_PAGE_TIMEOUT_SECONDS,remaining),
                )
            except asyncio.TimeoutError as exc:
                raise ValueError("Timed out fetching a required tick-history page") from exc
            if response.get("error"):
                raise ValueError(f"Tick history unavailable: {response['error']}")
            payload=response.get("history",{})
            epochs,prices=validate_ticks(payload.get("times",[]),payload.get("prices",[]))
            oldest=int(epochs[0])
            if previous_oldest is not None and oldest>=previous_oldest:
                raise ValueError("Tick history pagination did not advance")
            history.update(zip(epochs.tolist(),prices.tolist()))
            newest=max(int(epochs[-1]),max(history),self.ticks[-1][0] if self.ticks else 0)
            target=newest-required if since_epoch is None else max(int(since_epoch),newest-required)
            if oldest<=target:
                # Live ticks win overlap races and avoid a gap during long paging.
                history.update(self.ticks)
                self.ticks=deque(sorted(history.items()),maxlen=self.ticks.maxlen)
                return
            previous_oldest=oldest
            end=oldest-1
        raise ValueError("Tick history page limit reached before model lookback was covered")

    async def _repair_history(self):
        """Recover in the watcher task, never in the socket's tick callback.

        A callback awaiting history would block the same listener needed to
        deliver that response. Keep buffering live ticks while repair runs.
        """
        if not self._backfill_requested.is_set():
            return
        self.loading_history=True
        for attempt in range(MAX_HISTORY_RECOVERY_ATTEMPTS):
            generation=self._gap_generation
            try:
                await self._load_history(since_epoch=self._gap_epoch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error=f"Tick-history recovery failed: {exc}"
                if attempt+1==MAX_HISTORY_RECOVERY_ATTEMPTS:
                    raise ValueError(self.error) from exc
                delay=HISTORY_RECOVERY_BACKOFF_SECONDS*2**attempt
                self.reason=f"Recovering missing ticks; retrying in {delay:g} seconds"
                await asyncio.sleep(delay)
                continue
            self.error=None
            if generation==self._gap_generation:
                self._backfill_requested.clear(); self._gap_epoch=None
                self.loading_history=False
                self.reason="Waiting for the next 10-minute decision point"
            # A new live gap during this request needs another newest page.
            return

    def _outcome_arrays(self,decision):
        """Copy only this contract's path, rather than the full feature history."""
        first=int(decision)+DELAY-5
        last=int(decision)+DELAY+DURATION+5
        rows=[]
        for epoch,price in reversed(self.ticks):
            if epoch>last:
                continue
            if epoch<first:
                break
            rows.append((epoch,price))
        rows.reverse()
        return (np.array([row[0] for row in rows],dtype=np.int64),
                np.array([row[1] for row in rows],dtype=float))

    async def stop(self):
        async with self.lock:
            task=self.task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.reason="Stopped"
            return self.status()

    async def _run(self):
        from app.deriv.client import DerivWSClient
        self.client=DerivWSClient()
        try:
            await self.client.connect()
            self.loading_history=True
            await self.client.subscribe_ticks(SYMBOL,self.on_tick)
            await self._load_history()
            self.loading_history=self._backfill_requested.is_set()
            if not self.loading_history:
                self.reason="Waiting for the next 10-minute decision point"
            while True:
                try:
                    await asyncio.wait_for(self._backfill_requested.wait(),timeout=15)
                except asyncio.TimeoutError:
                    pass
                await self.client.ensure_connected()
                await self._repair_history()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error=str(exc); self.reason="Stopped after a data error; restart when data is available"
        finally:
            self.loading_history=False
            if self.client:
                await self.client.disconnect()

    async def on_tick(self, message):
        tick=message.get("tick",{})
        if tick.get("symbol") not in (None,SYMBOL):
            return
        epoch=int(tick.get("epoch",0)); price=float(tick.get("quote",float("nan")))
        if not np.isfinite(price) or price<=0 or not epoch:
            return
        if self.ticks and epoch<=self.ticks[-1][0]:
            return
        if self.ticks and epoch-self.ticks[-1][0]>5:
            gap=self.ticks[-1][0]
            self._gap_epoch=gap if self._gap_epoch is None else min(gap,self._gap_epoch)
            self._gap_generation+=1
            self._backfill_requested.set()
            self.loading_history=True
            self.reason="Recovering missing live ticks from public history"
        self.ticks.append((epoch,price)); self.last_tick={"epoch":epoch,"price":price}
        if self.loading_history:
            return
        from app.notifications.browser import send_notification

        for sid,signal in list(self.pending.items()):
            if epoch < signal["expiry_epoch"]:
                continue
            t,p=self._outcome_arrays(signal["decision_epoch"])
            labels=touch_labels(t,p,signal["decision_epoch"])
            touched=bool(labels[int(signal["direction"]=="lower")]) if labels is not None else None
            self._log({"type":"outcome","signal_id":sid,"touched":touched,"resolved_epoch":epoch})
            signal.update(outcome=touched,resolved=True,resolved_epoch=epoch)
            self.pending.pop(sid)
            # Outcome refreshes update chart markers without replaying an alert.
            await send_notification(
                "accuracy_outcome", {**signal,"touched":touched}, channels=("market",),
            )
        decision=epoch//600*600
        if epoch-decision>3 or decision<=self.last_evaluated:
            return
        if abs(time.time()-epoch)>5 or not 0 <= time.time()-decision <= 3:
            self.reason="Waiting for fresh ticks"; return
        t=np.array([v[0] for v in self.ticks],dtype=np.int64)
        p=np.array([v[1] for v in self.ticks])
        probability=self.bundle["predictor"].snapshot(t,p,decision)
        if probability is None:
            self.reason="Waiting for complete recent ticks and closed candles"; return
        if not 0 <= time.time()-decision <= 3:
            self.reason="Waiting for fresh ticks"; return
        self.last_evaluated=decision
        trend = 0
        try:
            from app.trade_prefs import load_trade_prefs
            if bool(load_trade_prefs().get("accuracy_trend_filter_enabled", True)):
                trend = market_trend_bias(t, p, decision)
        except Exception:
            trend = 0
        preferred = "upper" if probability[0] >= probability[1] else "lower"
        side = self.policy.choose(decision, probability, trend_bias=trend if trend else None)
        if side is None:
            if trend and (
                (trend < 0 and preferred == "upper") or (trend > 0 and preferred == "lower")
            ):
                self.reason = (
                    f"Skipped {preferred} against "
                    f"{'down' if trend < 0 else 'up'}trend (trend filter on)"
                )
            else:
                self.reason = "Waiting: confidence threshold, spacing, or daily cap"
            return
        report=self.bundle["report"]
        groups=report.get("feature_groups") or []
        groups=list(groups) if isinstance(groups,(list,tuple,dict)) else []
        signal={"type":"signal","signal_id":f"accuracy-{decision}","symbol":SYMBOL,
                "direction":side,"decision_epoch":decision,"processing_epoch":decision+DELAY,
                "expiry_epoch":decision+DELAY+DURATION,"duration_seconds":DURATION,
                "barrier_input":"+0.01" if side=="upper" else "-0.01",
                "estimated_probability":float(probability[int(side=="lower")]),
                "model":self.bundle["predictor"].name,"run_id":report["run_id"],
                "feature_profile":report.get("feature_profile","compact_v1"),"feature_groups":groups,
                "reference_price":price,"resolved":False,"outcome":None,
                "trend_bias":int(trend),
                "created_at":datetime.now(timezone.utc).isoformat(),
                "evaluation":"simulated 5-second entry delay; fresh forward touch outcome"}
        self._log(signal); self.pending[signal["signal_id"]]=signal; self.recent.append(signal)
        self.reason=f"Issued {side} Touch signal"
        await send_notification("accuracy_signal",signal,channels=("notifications","market"))
        await self._maybe_auto_trade(signal)

    async def _maybe_auto_trade(self, signal: dict) -> None:
        """Optional buy after an accuracy signal — same Setup gates as Dashboard auto-trade."""
        try:
            from app.database import async_session
            from app.deriv.auto_trade import maybe_auto_trade_after_signal
            from app.execution.gates import permission_status
            from app.trade_prefs import load_trade_prefs

            prefs = load_trade_prefs()
            if not prefs.get("accuracy_auto_trade_enabled"):
                return
            status = permission_status(prefs)
            if not status.get("allowed"):
                self._log({
                    "type": "auto_trade_skipped",
                    "signal_id": signal.get("signal_id"),
                    "reason": status.get("reason"),
                })
                return
            payload = {
                **signal,
                "touch_direction": signal.get("direction"),
                "is_validated": True,
                "research_only": False,
                "accuracy_mode": True,
            }
            async with async_session() as session:
                result = await maybe_auto_trade_after_signal(session, payload)
            self._log({
                "type": "auto_trade",
                "signal_id": signal.get("signal_id"),
                "ok": bool(result.get("ok")),
                "skipped": bool(result.get("skipped")),
                "reason": result.get("reason") or result.get("error"),
                "contract_id": result.get("contract_id"),
                "stake": result.get("stake"),
            })
            if result.get("ok"):
                signal["auto_trade"] = {
                    "ok": True,
                    "contract_id": result.get("contract_id"),
                    "stake": result.get("stake"),
                }
                self.reason = f"Issued {signal.get('direction')} Touch + auto-trade ok"
            elif not result.get("skipped"):
                signal["auto_trade"] = {
                    "ok": False,
                    "error": result.get("error") or result.get("reason"),
                }
        except Exception as exc:
            self.error = f"Auto-trade failed: {exc}"
            self._log({
                "type": "auto_trade",
                "signal_id": signal.get("signal_id"),
                "ok": False,
                "error": str(exc),
            })
