"""One feature/label contract shared by offline experiments and live signals."""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

SYMBOL = "R_100"
BARRIER = 0.01
DURATION = 540
DELAY = 5
SCHEMA = "r100-touch-001-next-tick-v1"
COLOMBO_OFFSET = 19800


def validate_ticks(epoch, price):
    t, p = np.asarray(epoch, dtype=np.int64), np.asarray(price, dtype=float)
    if t.ndim != 1 or p.ndim != 1 or len(t) != len(p) or not len(t):
        raise ValueError("Ticks must be nonempty, aligned one-dimensional arrays")
    if not np.isfinite(p).all() or (p <= 0).any() or (np.diff(t) <= 0).any():
        raise ValueError("Ticks must be finite, positive, unique and chronological")
    return t, p


def features(t: np.ndarray, p: np.ndarray, decision: int) -> dict | None:
    """Only prices available by decision; only completed 1m/5m candles."""
    end = int(np.searchsorted(t, decision, side="right"))
    start = int(np.searchsorted(t, decision - 900, side="right") - 1)
    if start < 0 or end <= start or decision - t[end - 1] > 5:
        return None
    ht, hp = t[start:end], p[start:end]
    if not np.isfinite(hp).all() or np.max(np.diff(ht)) > 5:
        return None
    out = {}
    for lag in (5, 15, 30, 60, 120, 300, 600):
        ix = int(np.searchsorted(ht, decision - lag, side="right") - 1)
        if ix < 0 or decision - lag - ht[ix] > 5:
            return None
        out[f"move_{lag}s"] = float(hp[-1] - hp[ix])
    for length in (60, 300, 600):
        q = hp[np.searchsorted(ht, decision - length):]
        if len(q) < 6:
            return None
        out[f"step_std_{length}s"] = float(np.std(np.diff(q)))
        out[f"range_{length}s"] = float(np.ptp(q))
    out["distance_in_step_std"] = BARRIER / max(out["step_std_300s"], 1e-9)
    for seconds in (60, 300):
        bars = []
        for lag in (0, 1):
            bar_end = decision // seconds * seconds - lag * seconds
            a = int(np.searchsorted(ht, bar_end - seconds))
            b = int(np.searchsorted(ht, bar_end))
            if b - a < 5 or ht[a] - (bar_end - seconds) > 5 or bar_end - ht[b-1] > 5:
                return None
            q = hp[a:b]
            o, h, l, c = float(q[0]), float(q.max()), float(q.min()), float(q[-1])
            bars.append((o, h, l, c))
            span = max(h - l, BARRIER)
            prefix = f"candle{seconds}_lag{lag}"
            out.update({prefix + "_body": c-o, prefix + "_upper_wick": h-max(o,c),
                        prefix + "_lower_wick": min(o,c)-l, prefix + "_range": h-l,
                        prefix + "_body_fraction": (c-o)/span,
                        prefix + "_close_position": (c-l)/span-.5})
        o,h,l,c = bars[0]; po,ph,pl,pc = bars[1]
        out[f"candle{seconds}_engulfing"] = float(
            (c-o)*(pc-po)<0 and max(o,c)>=max(po,pc) and min(o,c)<=min(po,pc)
        ) * float(np.sign(c-o))
        out[f"candle{seconds}_inside"] = float(h<=ph and l>=pl)
    return out


def touch_labels(t: np.ndarray, p: np.ndarray, decision: int) -> tuple[int, int] | None:
    """First post-processing tick anchors the barrier; duration starts at processing."""
    start, expiry = decision + DELAY, decision + DELAY + DURATION
    anchor = int(np.searchsorted(t, start, side="right"))
    beyond = int(np.searchsorted(t, expiry, side="left"))
    stop = int(np.searchsorted(t, expiry, side="right"))
    if anchor >= len(t) or beyond >= len(t) or t[anchor]-start > 5 or stop-anchor-1 < 5:
        return None
    if np.max(np.diff(t[max(0, anchor-1):beyond+1]), initial=0) > 5:
        return None
    # R_100 quotes are two-decimal prices; integer price points avoid binary-float touches.
    future = np.rint(p[anchor+1:stop] * 100).astype(np.int64)
    entry = int(round(float(p[anchor]) * 100))
    return int(np.any(future >= entry+1)), int(np.any(future <= entry-1))


def side_matrix(x: np.ndarray) -> np.ndarray:
    """The two targets are separate, potentially simultaneous touch events."""
    return np.concatenate([np.vstack([x,x]), np.r_[np.ones(len(x)),-np.ones(len(x))][:,None],
                           np.vstack([x,-x])], axis=1)


def wilson(wins: int, count: int) -> tuple[float, float]:
    if count <= 0:
        return 0.0, 1.0
    z = 1.959963984540054
    rate = wins/count; den = 1+z*z/count
    center = (rate+z*z/(2*count))/den
    half = z*math.sqrt(rate*(1-rate)/count+z*z/(4*count*count))/den
    return max(0.0, center-half), min(1.0, center+half)


@dataclass
class SignalPolicy:
    min_probability: float = .95
    max_per_day: int = 15
    cooldown_seconds: int = 5760
    last_epoch: int = -10**18
    counts: dict[int, int] = field(default_factory=dict)

    def __post_init__(self):
        if not .5 <= self.min_probability < 1 or not 1 <= self.max_per_day <= 15:
            raise ValueError("Probability must be [0.5,1); daily cap must be 1–15")
        if self.cooldown_seconds < 600:
            raise ValueError("Cooldown must be at least 600 seconds")

    def choose(self, epoch: int, probabilities) -> str | None:
        q = np.asarray(probabilities, dtype=float)
        if q.shape != (2,) or not np.isfinite(q).all() or (q < 0).any() or (q > 1).any():
            return None
        day = (int(epoch)+COLOMBO_OFFSET)//86400
        if q.max() < self.min_probability or self.counts.get(day, 0) >= self.max_per_day:
            return None
        if int(epoch)-self.last_epoch < self.cooldown_seconds:
            return None
        self.record(int(epoch))
        return "upper" if q[0] >= q[1] else "lower"

    def record(self, epoch: int):
        day = (int(epoch)+COLOMBO_OFFSET)//86400
        self.counts[day] = self.counts.get(day,0)+1
        self.last_epoch = max(self.last_epoch, int(epoch))
