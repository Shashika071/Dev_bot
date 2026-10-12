"""Stable multi-timeframe market state.

Closed 1m/5m/15m candles define direction. The 5-minute trend is the anchor.
Tick movement can strengthen or weaken that score, but it cannot flip it.
A state changes only after several new closed bars agree, and a reversal
must pass through neutral first.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from app.config import settings
from app.strategies.candle_confirm import _drop_forming_candle, _ema


@dataclass
class MarketStateConfig:
    enter: float = 0.35
    exit: float = 0.12
    confirm_bars: int = 2
    weight_1m: float = 0.20
    weight_5m: float = 0.50
    weight_15m: float = 0.30
    conflict_gap: float = 0.20


@dataclass
class MarketSnapshot:
    state: str
    score: float
    components: dict
    stable_since: Optional[str]
    fresh: bool
    confirmed_strong: bool
    bar_id: Optional[int] = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "score": self.score,
            "components": self.components,
            "stable_since": self.stable_since,
            "fresh": self.fresh,
            "confirmed_strong": self.confirmed_strong,
            "bar_id": self.bar_id,
            "reason": self.reason,
        }


def _clip(value: float) -> float:
    if value != value:
        return 0.0
    return float(np.tanh(value))


def timeframe_strength(candles: list[dict]) -> float:
    """Signed strength in [-1, 1]. Positive means upper."""
    if len(candles) < 22:
        return 0.0
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    fast = _ema(closes, 8)
    slow = _ema(closes, 21)
    if not fast or not slow:
        return 0.0
    window = min(10, len(highs))
    recent_range = max(max(highs[-window:]) - min(lows[-window:]), 1e-9)
    spread = (fast[-1] - slow[-1]) / recent_range
    slope = (fast[-1] - fast[max(0, len(fast) - 4)]) / recent_range
    ret = (closes[-1] - closes[-6]) / recent_range if closes[-6] else 0.0
    structure = 0.0
    if highs[-1] > highs[-3] and lows[-1] >= lows[-3]:
        structure = 1.0
    elif lows[-1] < lows[-3] and highs[-1] <= highs[-3]:
        structure = -1.0
    raw = 0.45 * _clip(spread) + 0.25 * _clip(slope) + 0.20 * structure + 0.10 * _clip(ret)
    return float(max(-1.0, min(1.0, raw)))


def _closed(candles: list[dict] | None, *, official: bool) -> list[dict]:
    rows = list(candles or [])
    if official:
        return _drop_forming_candle(rows)
    return rows


def _tick_modifier(features_df) -> float:
    if features_df is None or getattr(features_df, "empty", True):
        return 0.0
    row = features_df.iloc[-1]
    for name in ("return_t300", "return_300", "return_t60"):
        try:
            value = row.get(name) if hasattr(row, "get") else row[name]
        except (KeyError, TypeError):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number and number != 0.0:
            return number
    return 0.0


def fuse_trend_scores(
    candles_1m: list[dict],
    candles_5m: list[dict],
    candles_15m: list[dict],
    *,
    tick_return: float = 0.0,
    config: MarketStateConfig | None = None,
) -> tuple[float, bool, dict]:
    cfg = config or MarketStateConfig()
    s1 = timeframe_strength(candles_1m)
    s5 = timeframe_strength(candles_5m)
    s15 = timeframe_strength(candles_15m)
    fused = cfg.weight_1m * s1 + cfg.weight_5m * s5 + cfg.weight_15m * s15
    tick_sign = 0.0 if tick_return == 0 else float(np.sign(tick_return))
    fused_sign = 0.0 if abs(fused) < 1e-9 else float(np.sign(fused))
    conflicted = False
    if tick_sign != 0.0 and fused_sign != 0.0 and tick_sign != fused_sign and abs(fused) >= cfg.conflict_gap:
        conflicted = True
        fused *= 0.5
    elif tick_sign != 0.0 and tick_sign == fused_sign:
        fused = float(np.clip(fused * 1.05, -1.0, 1.0))
    higher_conflict = (
        abs(s5) >= cfg.conflict_gap
        and abs(s15) >= cfg.conflict_gap
        and float(np.sign(s5)) != float(np.sign(s15))
    )
    components = {
        "1m": s1,
        "5m": s5,
        "15m": s15,
        "fused": fused,
        "tick_return": tick_return,
        "higher_timeframe_conflict": higher_conflict,
    }
    return float(fused), bool(conflicted or higher_conflict), components


class MarketStateTracker:
    def __init__(self, config: MarketStateConfig | None = None, path: str | None = None):
        self.config = config or MarketStateConfig()
        self.path = path
        self.state = "NEUTRAL"
        self.pending: str | None = None
        self.pending_count = 0
        self.stable_since: str | None = None
        self.last_bar_id: int | None = None
        self.last_score = 0.0

    def update(
        self,
        score: float,
        *,
        conflicted: bool,
        bar_id: int | None,
        fresh: bool,
        components: dict | None = None,
    ) -> MarketSnapshot:
        components = components or {}
        if not fresh:
            return self._snapshot(score, False, components, "stale_or_insufficient_candles")
        if bar_id is not None and bar_id == self.last_bar_id:
            return self._snapshot(score, True, components, "same_closed_bar")

        desired = self._desired(score, conflicted)
        if self.state == "UPPER" and desired == "LOWER":
            desired = "NEUTRAL"
        elif self.state == "LOWER" and desired == "UPPER":
            desired = "NEUTRAL"

        if desired == self.state:
            self.pending = None
            self.pending_count = 0
        else:
            if self.pending == desired:
                self.pending_count += 1
            else:
                self.pending = desired
                self.pending_count = 1
            if self.pending_count >= int(self.config.confirm_bars):
                self.state = desired
                self.stable_since = datetime.now(timezone.utc).isoformat()
                self.pending = None
                self.pending_count = 0
        self.last_bar_id = bar_id
        self.last_score = float(score)
        self.save()
        return self._snapshot(score, True, components, "updated")

    def _desired(self, score: float, conflicted: bool) -> str:
        if conflicted:
            return "CONFLICTED"
        if score >= self.config.enter:
            return "UPPER"
        if score <= -self.config.enter:
            return "LOWER"
        if self.state == "UPPER" and score >= self.config.exit:
            return "UPPER"
        if self.state == "LOWER" and score <= -self.config.exit:
            return "LOWER"
        return "NEUTRAL"

    def _snapshot(self, score: float, fresh: bool, components: dict, reason: str) -> MarketSnapshot:
        strong = fresh and self.state in ("UPPER", "LOWER")
        return MarketSnapshot(
            state=self.state,
            score=float(score),
            components=components,
            stable_since=self.stable_since,
            fresh=fresh,
            confirmed_strong=strong,
            bar_id=self.last_bar_id,
            reason=reason,
        )

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "state": self.state,
                        "pending": self.pending,
                        "pending_count": self.pending_count,
                        "stable_since": self.stable_since,
                        "last_bar_id": self.last_bar_id,
                        "last_score": self.last_score,
                    },
                    handle,
                )
        except OSError:
            return

    @classmethod
    def load(cls, path: str | None, config: MarketStateConfig | None = None) -> "MarketStateTracker":
        tracker = cls(config=config, path=path)
        if not path or not os.path.isfile(path):
            return tracker
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return tracker
        tracker.state = str(payload.get("state") or "NEUTRAL")
        tracker.pending = payload.get("pending")
        tracker.pending_count = int(payload.get("pending_count") or 0)
        tracker.stable_since = payload.get("stable_since")
        tracker.last_bar_id = payload.get("last_bar_id")
        tracker.last_score = float(payload.get("last_score") or 0.0)
        return tracker


_TRACKER: MarketStateTracker | None = None


def default_state_path() -> str | None:
    try:
        os.makedirs(settings.model_dir, exist_ok=True)
    except OSError:
        return None
    return os.path.join(settings.model_dir, "market_state.json")


def get_market_tracker() -> MarketStateTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = MarketStateTracker.load(default_state_path())
    return _TRACKER


def reset_market_tracker(tracker: MarketStateTracker | None = None) -> None:
    global _TRACKER
    _TRACKER = tracker


def evaluate_market_state(
    *,
    official_candles: dict | None = None,
    features_df=None,
    tracker: MarketStateTracker | None = None,
    persist: bool = True,
    config: MarketStateConfig | None = None,
) -> MarketSnapshot:
    cfg = config or MarketStateConfig()
    state_tracker = tracker if tracker is not None else (get_market_tracker() if persist else MarketStateTracker(cfg))
    if config is not None and tracker is None and not persist:
        state_tracker.config = config
    candles = official_candles or {}
    official = str(candles.get("source") or "") == "deriv_official" or bool(candles.get("1m"))
    c1 = _closed(candles.get("1m"), official=official and str(candles.get("source") or "") == "deriv_official")
    c5 = _closed(candles.get("5m"), official=official and str(candles.get("source") or "") == "deriv_official")
    c15 = _closed(candles.get("15m"), official=official and str(candles.get("source") or "") == "deriv_official")
    fresh = len(c5) >= 22 and len(c1) >= 22
    if not fresh:
        return state_tracker.update(
            0.0,
            conflicted=False,
            bar_id=None,
            fresh=False,
            components={"1m": 0.0, "5m": 0.0, "15m": 0.0},
        )
    score, conflicted, components = fuse_trend_scores(
        c1,
        c5,
        c15,
        tick_return=_tick_modifier(features_df),
        config=state_tracker.config,
    )
    try:
        bar_id = int(float(c5[-1].get("epoch") or c5[-1].get("open_time") or len(c5)))
    except (TypeError, ValueError):
        bar_id = len(c5)
    return state_tracker.update(
        score,
        conflicted=conflicted,
        bar_id=bar_id,
        fresh=True,
        components=components,
    )


def direction_blocked_by_trend(
    direction: str,
    snapshot: MarketSnapshot | None,
    *,
    enabled: bool = True,
) -> tuple[bool, str]:
    """Block only a fresh, confirmed trend that is strongly opposite."""
    if not enabled or snapshot is None:
        return False, "trend_gate_off"
    if not snapshot.fresh or not snapshot.confirmed_strong:
        return False, snapshot.state.lower() if snapshot.state else "unavailable"
    opposite = {"UPPER": "lower", "LOWER": "upper"}.get(snapshot.state)
    if opposite and str(direction).lower() == opposite:
        return True, f"strong_opposite_{snapshot.state.lower()}"
    return False, "not_opposite"
