"""Causal technical inputs for the optional expanded Touch Accuracy profile.

These are price features, not trading rules or probability estimates. In
particular, ``sweep`` means a price excursion/rejection of a prior range; it
does not establish that stop orders or liquidity were present.

Only completed UTC-aligned 1m/5m/15m/1h candles are used. A candle covering
[start, end) becomes available at end; an epoch exactly at end belongs to the
next candle. Pivots need two completed bars on each side and are exposed only
when the second right-hand bar closes. No pattern is backdated to its pivot.

Snapshots use at most twelve hours, with ten continuous hours required.
Ticks older than five seconds, gaps over five seconds within the last ten
hours, missing candles, or insufficient history return None. Older gaps reset
the usable history; nothing is forward-filled. The same bounded snapshot
calculation is used for a full offline context and a live tick window.

For 1m/5m/15m, Wilder-style smoothing uses alpha=1/14 and MACD is
12/26/9, with at least 35 completed candles. The 1h context has EMA3/6
trend and price geometry, with no standard RSI/MACD/ADX outputs. EMA
recurrences start at the first observation in the bounded window, rather
than an unbounded historical seed. Periods are research inputs, not optima.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter

HISTORY_SECONDS = 12 * 3600
MIN_HISTORY_SECONDS = 10 * 3600
MAX_GAP_SECONDS = 5
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
FEATURE_GROUPS = {
    "trend": ["tech_trend_"],
    "structure": ["tech_structure_"],
    "multi_timeframe": ["tech_mtf_"],
    "support_resistance": ["tech_sr_"],
    "sweep_proxies": ["tech_sweep_"],
    "breakouts_retests": ["tech_breakout_"],
    "candlestick": ["tech_candle_"],
    "indicators": ["tech_indicator_"],
    "momentum_confirmation": ["tech_momentum_"],
}
_EPS = 1e-9


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    return lfilter([alpha], [1.0, -(1.0 - alpha)], values,
                   zi=[float(values[0]) * (1.0 - alpha)])[0]


@dataclass
class _Candles:
    start: np.ndarray
    end: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    valid: np.ndarray


class TechnicalContext:
    """Aggregate each timeframe once; reuse snapshot for offline decisions.

    Inputs must contain unique chronological integer-second epochs and finite
    positive prices. They are read-only from this class's perspective; callers
    must not mutate their arrays while reusing a context.
    """

    def __init__(self, t, p):
        raw_t, price = np.asarray(t), np.asarray(p, dtype=float)
        if raw_t.ndim != 1 or price.ndim != 1 or len(raw_t) != len(price) or not len(raw_t):
            raise ValueError("Ticks must be nonempty aligned one-dimensional arrays")
        if not np.issubdtype(raw_t.dtype, np.number) or not np.isfinite(raw_t).all():
            raise ValueError("Tick epochs must be finite integer seconds")
        epoch = raw_t.astype(np.int64, copy=False)
        if not np.array_equal(raw_t, epoch) or (np.diff(epoch) <= 0).any():
            raise ValueError("Tick epochs must be unique chronological integer seconds")
        if not np.isfinite(price).all() or (price <= 0).any():
            raise ValueError("Tick prices must be finite and positive")
        self.t, self.p = epoch, price
        self._gaps = np.flatnonzero(np.diff(epoch) > MAX_GAP_SECONDS)
        self._candles = {name: self._aggregate(seconds) for name, seconds in TIMEFRAMES.items()}

    def _aggregate(self, seconds: int) -> _Candles:
        bucket = self.t // seconds
        starts = np.r_[0, np.flatnonzero(bucket[1:] != bucket[:-1]) + 1]
        stops = np.r_[starts[1:], len(self.t)]
        begin = bucket[starts] * seconds
        end = begin + seconds
        good = ((self.t[starts] - begin <= MAX_GAP_SECONDS)
                & (end - self.t[stops - 1] <= MAX_GAP_SECONDS)
                & (stops - starts >= 2))
        left = np.searchsorted(self._gaps, starts)
        right = np.searchsorted(self._gaps, stops - 1)
        good &= left == right
        return _Candles(begin, end, self.p[starts], np.maximum.reduceat(self.p, starts),
                        np.minimum.reduceat(self.p, starts), self.p[stops - 1], good)

    def snapshot(self, decision: int) -> dict[str, float] | None:
        decision = int(decision)
        stop = int(np.searchsorted(self.t, decision, side="right"))
        if stop == 0 or decision - self.t[stop - 1] > MAX_GAP_SECONDS:
            return None
        start = int(np.searchsorted(self.t, decision - HISTORY_SECONDS, side="left"))
        gap = int(np.searchsorted(self._gaps, stop - 1, side="left") - 1)
        if gap >= 0:
            start = max(start, int(self._gaps[gap]) + 1)
        if start >= stop or decision - int(self.t[start]) < MIN_HISTORY_SECONDS:
            return None
        available_start = int(self.t[start])
        out: dict[str, float] = {}
        trend_signs = {}
        for name, seconds in TIMEFRAMES.items():
            candles = self._candles[name]
            a = int(np.searchsorted(candles.start, available_start, side="left"))
            b = int(np.searchsorted(candles.end, decision, side="right"))
            if b - a < (6 if name == "1h" else 35):
                return None
            if not candles.valid[a:b].all() or (np.diff(candles.start[a:b]) != seconds).any():
                return None
            bars = (candles.open[a:b], candles.high[a:b], candles.low[a:b], candles.close[a:b])
            values, sign = _timeframe_features(name, *bars)
            out.update(values)
            trend_signs[name] = sign
        signs = np.asarray(list(trend_signs.values()))
        out["tech_mtf_bullish_count"] = float((signs > 0).sum())
        out["tech_mtf_bearish_count"] = float((signs < 0).sum())
        out["tech_mtf_signed_alignment"] = float(signs.sum())
        for fast, slow in (("1m", "5m"), ("5m", "15m"), ("15m", "1h")):
            out[f"tech_mtf_{fast}_{slow}_agreement"] = float(trend_signs[fast] * trend_signs[slow])
        if not all(np.isfinite(value) for value in out.values()):
            return None
        return out


def technical_features(t, p, decision: int) -> dict[str, float] | None:
    """Live convenience wrapper; offline callers should reuse TechnicalContext."""
    return TechnicalContext(t, p).snapshot(decision)


def _confirmed_structure(high, low, close) -> dict[str, float]:
    """Two-sided pivots become usable at confirmation, never at their center."""
    last_h = prev_h = last_l = prev_l = None
    high_at = low_at = -1
    state = 0
    last_bos = last_choch = -1
    bos = choch = 0
    for i in range(len(close)):
        bos = choch = 0
        # Current close can break only a level confirmed BEFORE this bar.
        if i and last_h is not None and close[i] > last_h and close[i - 1] <= last_h:
            bos = 1
        elif i and last_l is not None and close[i] < last_l and close[i - 1] >= last_l:
            bos = -1
        if bos:
            last_bos = i
            if state and bos != state:
                choch = bos
                last_choch = i
        if i >= 4:
            center = i - 2
            if high[center] > max(high[center - 2], high[center - 1], high[center + 1], high[center + 2]):
                prev_h, last_h, high_at = last_h, float(high[center]), center
            if low[center] < min(low[center - 2], low[center - 1], low[center + 1], low[center + 2]):
                prev_l, last_l, low_at = last_l, float(low[center]), center
            if prev_h is not None and prev_l is not None:
                if last_h > prev_h and last_l > prev_l:
                    state = 1
                elif last_h < prev_h and last_l < prev_l:
                    state = -1
                else:
                    state = 0
    n = len(close)
    return {"has_swing_high": float(last_h is not None), "has_swing_low": float(last_l is not None),
            "higher_high": float(prev_h is not None and last_h > prev_h),
            "lower_high": float(prev_h is not None and last_h < prev_h),
            "higher_low": float(prev_l is not None and last_l > prev_l),
            "lower_low": float(prev_l is not None and last_l < prev_l),
            "state": float(state), "bos": float(bos), "choch": float(choch),
            "bars_since_bos": float(n - 1 - last_bos if last_bos >= 0 else n),
            "bars_since_choch": float(n - 1 - last_choch if last_choch >= 0 else n),
            "bars_since_swing_high": float(n - 1 - high_at if high_at >= 0 else n),
            "bars_since_swing_low": float(n - 1 - low_at if low_at >= 0 else n)}


def _timeframe_features(name, open_, high, low, close):
    out = {}
    def put(group, **values):
        out.update({f"tech_{group}_{name}_{key}": float(value) for key, value in values.items()})
    delta = np.diff(close, prepend=close[0])
    previous = np.r_[close[0], close[:-1]]
    true_range = np.maximum(high - low, np.maximum(np.abs(high - previous), np.abs(low - previous)))
    wilder_span = 9 if name == "1h" else 27
    atr = float(_ema(true_range, wilder_span)[-1])
    scale = max(atr, _EPS)
    fast, slow = (3, 6) if name == "1h" else (8, 21)
    ef, es = _ema(close, fast), _ema(close, slow)
    sign = int(np.sign(ef[-1] - es[-1]))
    look = min(10, len(close))
    centered = np.arange(look) - (look - 1) / 2
    slope = float(np.dot(centered, close[-look:]) / np.dot(centered, centered))
    path = float(np.abs(np.diff(close[-look:])).sum())
    put("trend", ema_gap_atr=(ef[-1]-es[-1])/scale, slow_distance_atr=(close[-1]-es[-1])/scale,
        slow_slope_atr=(es[-1]-es[-4])/(3*scale), regression_slope_atr=slope/scale,
        efficiency=abs(close[-1]-close[-look])/max(path,_EPS))
    put("structure", **_confirmed_structure(high, low, close))

    width = min(20, len(close)-2)
    resistance = float(high[-width-1:-1].max())
    support = float(low[-width-1:-1].min())
    tolerance = .15*scale
    put("sr", resistance_distance_atr=(resistance-close[-1])/scale,
        support_distance_atr=(close[-1]-support)/scale,
        resistance_contacts=np.sum(np.abs(high[-width-1:-1]-resistance)<=tolerance),
        support_contacts=np.sum(np.abs(low[-width-1:-1]-support)<=tolerance))
    put("sweep", upper_range_rejection=high[-1]>resistance and close[-1]<=resistance,
        lower_range_rejection=low[-1]<support and close[-1]>=support)
    prior_res = float(high[-width-2:-2].max())
    prior_sup = float(low[-width-2:-2].min())
    put("breakout", upper_close_break=close[-1]>resistance,
        lower_close_break=close[-1]<support,
        upper_retest=(close[-2]>prior_res and low[-1]<=prior_res+tolerance and close[-1]>=prior_res),
        lower_retest=(close[-2]<prior_sup and high[-1]>=prior_sup-tolerance and close[-1]<=prior_sup))

    body = float(close[-1]-open_[-1]); old_body = float(close[-2]-open_[-2])
    span = float(high[-1]-low[-1]); safe_span = max(span,_EPS)
    uw = float(high[-1]-max(open_[-1],close[-1])); lw = float(min(open_[-1],close[-1])-low[-1])
    engulf = (body*old_body<0 and max(open_[-1],close[-1])>=max(open_[-2],close[-2])
              and min(open_[-1],close[-1])<=min(open_[-2],close[-2]))
    put("candle", body_atr=body/scale, range_atr=span/scale, upper_wick_fraction=uw/safe_span,
        lower_wick_fraction=lw/safe_span, body_fraction=body/safe_span,
        close_position=(close[-1]-low[-1])/safe_span-.5 if span else 0,
        engulfing=np.sign(body)*engulf, inside=high[-1]<=high[-2] and low[-1]>=low[-2],
        outside=high[-1]>high[-2] and low[-1]<low[-2], doji=abs(body)<=.1*span,
        hammer=abs(body)>_EPS and lw>=2*abs(body) and uw<=abs(body),
        shooting_star=abs(body)>_EPS and uw>=2*abs(body) and lw<=abs(body))

    confirmation = {}
    if name != "1h":
        gain = float(_ema(np.maximum(delta,0),wilder_span)[-1]); loss = float(_ema(np.maximum(-delta,0),wilder_span)[-1])
        rsi = 50. if gain+loss<=_EPS else (100. if loss<=_EPS else 100.-100./(1.+gain/loss))
        macd = _ema(close,12)-_ema(close,26)
        signal = _ema(macd,9)
        up = np.diff(high,prepend=high[0]); down = -np.diff(low,prepend=low[0])
        pdm = _ema(np.where((up>down)&(up>0),up,0.),wilder_span)
        mdm = _ema(np.where((down>up)&(down>0),down,0.),wilder_span)
        smooth_tr = _ema(true_range,wilder_span)
        plus_di=np.divide(100*pdm,smooth_tr,out=np.zeros(len(close)),where=smooth_tr>_EPS)
        minus_di=np.divide(100*mdm,smooth_tr,out=np.zeros(len(close)),where=smooth_tr>_EPS)
        total=plus_di+minus_di
        dx=np.divide(100*np.abs(plus_di-minus_di),total,out=np.zeros(len(close)),where=total>_EPS)
        adx=float(_ema(dx,wilder_span)[-1])
        bb=close[-min(20,len(close)):]; sd=float(np.std(bb)); mean=float(np.mean(bb))
        put("indicator", rsi=rsi, macd_atr=macd[-1]/scale, macd_signal_atr=signal[-1]/scale,
            macd_histogram_atr=(macd[-1]-signal[-1])/scale, atr=atr,
            adx=adx, plus_di=plus_di[-1], minus_di=minus_di[-1],
            bollinger_z=(close[-1]-mean)/max(sd,_EPS), bollinger_width_atr=4*sd/scale)
        confirmation = {"trend_macd_agreement":sign*np.sign(macd[-1]-signal[-1]),
                        "trend_rsi_agreement":sign*np.sign(rsi-50.)}
    momentum = {f"move_{lag}_bars_atr": (close[-1]-close[-lag-1])/scale for lag in (1,3,5) if lag<len(close)}
    put("momentum", **momentum, acceleration_atr=(delta[-1]-delta[-2])/scale,
        up_fraction_5=float(np.mean(delta[-5:]>0)), down_fraction_5=float(np.mean(delta[-5:]<0)),
        **confirmation)
    return out, sign


# Stable order for schema/profile validation without reading a market feed.
# Six or more 1h bars always provide the same named outputs as this template.
_schema_bar = np.ones(40, dtype=float)
FEATURE_NAMES = tuple(
    key for timeframe in TIMEFRAMES
    for key in _timeframe_features(timeframe, _schema_bar, _schema_bar, _schema_bar, _schema_bar)[0]
) + (
    "tech_mtf_bullish_count", "tech_mtf_bearish_count", "tech_mtf_signed_alignment",
    "tech_mtf_1m_5m_agreement", "tech_mtf_5m_15m_agreement", "tech_mtf_15m_1h_agreement",
)
del _schema_bar
