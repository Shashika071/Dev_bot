"""
Candle confirmation layer for One-Touch signals.

Prefers Deriv official 1m + 5m + 15m OHLC; falls back to tick-aggregated
candles if the public candle API is unavailable. Scores trend + classic
candlestick patterns (engulfing, stars, harami, tweezers, soldiers/crows,
abandoned baby, three methods, etc.). Extra gate on top of tick confluence
+ ML confidence — does not replace tick training.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
import structlog

from app.signal_engine.chart_candles import aggregate_ohlc

logger = structlog.get_logger(__name__)


@dataclass
class CandleConfirmResult:
    confirmed: bool
    direction: str
    score: float
    opposite_score: float
    explanation: str
    details: dict


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(alpha * float(v) + (1 - alpha) * out[-1])
    return out


def _rsi_last(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    arr = np.asarray(closes[-(period + 1) :], dtype=float)
    deltas = np.diff(arr)
    gains = np.clip(deltas, 0, None)
    losses = np.clip(-deltas, 0, None)
    avg_gain = float(gains.mean()) if len(gains) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _is_bull(o: float, c: float) -> bool:
    return c > o


def _is_bear(o: float, c: float) -> bool:
    return c < o


def _is_doji(o: float, h: float, l: float, c: float, thr: float = 0.12) -> bool:
    return _body(o, c) / _range(h, l) <= thr


def _score_patterns(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    direction: str,
) -> tuple[float, list[str]]:
    """Full classic OHLC pattern set — confirmation points for upper/lower."""
    score = 0.0
    reasons: list[str] = []
    n = len(opens)
    if n < 2:
        return 0.0, ["need ≥2 candles for patterns"]

    o0, h0, l0, c0 = opens[-1], highs[-1], lows[-1], closes[-1]
    o1, h1, l1, c1 = opens[-2], highs[-2], lows[-2], closes[-2]
    rng0 = _range(h0, l0)
    rng1 = _range(h1, l1)
    body0 = _body(o0, c0)
    body1 = _body(o1, c1)
    uw0 = h0 - max(o0, c0)
    lw0 = min(o0, c0) - l0
    uw1 = h1 - max(o1, c1)
    lw1 = min(o1, c1) - l1
    bull0, bear0 = _is_bull(o0, c0), _is_bear(o0, c0)
    bull1, bear1 = _is_bull(o1, c1), _is_bear(o1, c1)
    doji0 = _is_doji(o0, h0, l0, c0)

    # --- Single-candle ---
    close_pos = (c0 - l0) / rng0
    if direction == "upper" and close_pos >= 0.7:
        score += 1.0
        reasons.append("close near high")
    elif direction == "lower" and close_pos <= 0.3:
        score += 1.0
        reasons.append("close near low")

    if body0 / rng0 >= 0.7 and uw0 <= 0.1 * rng0 and lw0 <= 0.1 * rng0:
        if direction == "upper" and bull0:
            score += 1.4
            reasons.append("bullish marubozu")
        elif direction == "lower" and bear0:
            score += 1.4
            reasons.append("bearish marubozu")
    elif body0 / rng0 >= 0.65:
        if direction == "upper" and bull0:
            score += 1.0
            reasons.append("strong bull body")
        elif direction == "lower" and bear0:
            score += 1.0
            reasons.append("strong bear body")

    # Hammer / hanging man / inverted hammer / shooting star
    if lw0 >= 2.0 * max(body0, 1e-12) and uw0 <= body0:
        if direction == "upper":
            score += 1.5
            reasons.append("hammer/pin")
        # hanging man is bearish only after advance — treat as mild lower if prior up
        elif direction == "lower" and n >= 4 and closes[-2] > closes[-4]:
            score += 1.0
            reasons.append("hanging-man")
    if uw0 >= 2.0 * max(body0, 1e-12) and lw0 <= body0:
        if direction == "lower":
            score += 1.5
            reasons.append("shooting-star/pin")
        elif direction == "upper" and n >= 4 and closes[-2] < closes[-4]:
            score += 1.0
            reasons.append("inverted-hammer")

    # Spinning top = indecision (note only)
    if 0.12 < body0 / rng0 <= 0.35 and uw0 > body0 and lw0 > body0:
        reasons.append("spinning-top")

    if doji0:
        reasons.append("doji/indecision")

    # Dragonfly doji (long lower wick) / gravestone doji (long upper wick)
    if doji0 and lw0 >= 2.5 * max(body0, 1e-12) and uw0 <= body0 and direction == "upper":
        score += 1.6
        reasons.append("dragonfly-doji")
    if doji0 and uw0 >= 2.5 * max(body0, 1e-12) and lw0 <= body0 and direction == "lower":
        score += 1.6
        reasons.append("gravestone-doji")

    # Belt hold
    if direction == "upper" and bull0 and lw0 <= 0.08 * rng0 and body0 / rng0 >= 0.55:
        score += 1.2
        reasons.append("bullish belt-hold")
    if direction == "lower" and bear0 and uw0 <= 0.08 * rng0 and body0 / rng0 >= 0.55:
        score += 1.2
        reasons.append("bearish belt-hold")

    # --- Two-candle ---
    # Engulfing
    if (
        direction == "upper"
        and bear1
        and bull0
        and c0 >= o1
        and o0 <= c1
        and body0 > body1
    ):
        score += 2.0
        reasons.append("bullish engulfing")
    if (
        direction == "lower"
        and bull1
        and bear0
        and c0 <= o1
        and o0 >= c1
        and body0 > body1
    ):
        score += 2.0
        reasons.append("bearish engulfing")

    # Harami (inside body)
    if (
        direction == "upper"
        and bear1
        and bull0
        and max(o0, c0) < max(o1, c1)
        and min(o0, c0) > min(o1, c1)
        and body0 < body1 * 0.7
    ):
        score += 1.4
        reasons.append("bullish harami")
    if (
        direction == "lower"
        and bull1
        and bear0
        and max(o0, c0) < max(o1, c1)
        and min(o0, c0) > min(o1, c1)
        and body0 < body1 * 0.7
    ):
        score += 1.4
        reasons.append("bearish harami")

    # Harami cross (doji inside prior body)
    if doji0 and max(o0, c0) < max(o1, c1) and min(o0, c0) > min(o1, c1):
        if direction == "upper" and bear1:
            score += 1.5
            reasons.append("bullish harami-cross")
        if direction == "lower" and bull1:
            score += 1.5
            reasons.append("bearish harami-cross")

    # Piercing line / dark cloud cover
    mid1 = (o1 + c1) / 2.0
    if (
        direction == "upper"
        and bear1
        and bull0
        and o0 < l1
        and c0 > mid1
        and c0 < o1
    ):
        score += 1.8
        reasons.append("piercing-line")
    if (
        direction == "lower"
        and bull1
        and bear0
        and o0 > h1
        and c0 < mid1
        and c0 > o1
    ):
        score += 1.8
        reasons.append("dark-cloud-cover")

    # Tweezer tops / bottoms
    if abs(l0 - l1) <= 0.15 * max(rng0, rng1) and direction == "upper" and bear1 and bull0:
        score += 1.3
        reasons.append("tweezer-bottom")
    if abs(h0 - h1) <= 0.15 * max(rng0, rng1) and direction == "lower" and bull1 and bear0:
        score += 1.3
        reasons.append("tweezer-top")

    # Kicker
    if direction == "upper" and bear1 and bull0 and o0 > o1 and c0 > o0:
        score += 2.0
        reasons.append("bullish kicker")
    if direction == "lower" and bull1 and bear0 and o0 < o1 and c0 < o0:
        score += 2.0
        reasons.append("bearish kicker")

    # Outside bar (engulfing range)
    if h0 > h1 and l0 < l1:
        if direction == "upper" and bull0:
            score += 1.4
            reasons.append("bullish outside-bar")
        if direction == "lower" and bear0:
            score += 1.4
            reasons.append("bearish outside-bar")

    # --- Three-candle ---
    if n >= 3:
        o2, h2, l2, c2 = opens[-3], highs[-3], lows[-3], closes[-3]
        body2 = _body(o2, c2)
        bull2, bear2 = _is_bull(o2, c2), _is_bear(o2, c2)
        doji1 = _is_doji(o1, h1, l1, c1)

        # Inside bar then break (mother = -3, inside = -2, break = -1)
        inside = h1 <= h2 and l1 >= l2
        if inside and direction == "upper" and c0 > h1:
            score += 1.5
            reasons.append("inside-bar upside break")
        if inside and direction == "lower" and c0 < l1:
            score += 1.5
            reasons.append("inside-bar downside break")

        # Morning star / evening star
        small_mid = body1 <= 0.35 * max(body2, body0, 1e-12) or doji1
        if (
            direction == "upper"
            and bear2
            and small_mid
            and bull0
            and c0 > mid1
            and c0 > (o2 + c2) / 2.0
        ):
            score += 2.2
            reasons.append("morning-star")
        if (
            direction == "lower"
            and bull2
            and small_mid
            and bear0
            and c0 < mid1
            and c0 < (o2 + c2) / 2.0
        ):
            score += 2.2
            reasons.append("evening-star")

        # Three white soldiers / three black crows
        if (
            direction == "upper"
            and bull2
            and bull1
            and bull0
            and closes[-1] > closes[-2] > closes[-3]
            and opens[-1] > opens[-2] > opens[-3]
        ):
            score += 2.0
            reasons.append("three white soldiers")
        if (
            direction == "lower"
            and bear2
            and bear1
            and bear0
            and closes[-1] < closes[-2] < closes[-3]
            and opens[-1] < opens[-2] < opens[-3]
        ):
            score += 2.0
            reasons.append("three black crows")

        # Three inside up / down (harami + confirm)
        harami_up = (
            bear2
            and bull1
            and max(o1, c1) < max(o2, c2)
            and min(o1, c1) > min(o2, c2)
        )
        harami_dn = (
            bull2
            and bear1
            and max(o1, c1) < max(o2, c2)
            and min(o1, c1) > min(o2, c2)
        )
        if direction == "upper" and harami_up and bull0 and c0 > c2:
            score += 1.8
            reasons.append("three-inside-up")
        if direction == "lower" and harami_dn and bear0 and c0 < c2:
            score += 1.8
            reasons.append("three-inside-down")

        # Three outside up / down (engulf + confirm)
        eng_up = bear2 and bull1 and c1 >= o2 and o1 <= c2 and _body(o1, c1) > body2
        eng_dn = bull2 and bear1 and c1 <= o2 and o1 >= c2 and _body(o1, c1) > body2
        if direction == "upper" and eng_up and bull0 and c0 > c1:
            score += 1.8
            reasons.append("three-outside-up")
        if direction == "lower" and eng_dn and bear0 and c0 < c1:
            score += 1.8
            reasons.append("three-outside-down")

        # Abandoned baby (gap doji) — approximate with gaps around small mid
        gap_down = h1 < l2
        gap_up = l1 > h2
        gap_up_out = l0 > h1
        gap_dn_out = h0 < l1
        if direction == "upper" and bear2 and doji1 and gap_down and bull0 and gap_up_out:
            score += 2.4
            reasons.append("bullish abandoned-baby")
        if direction == "lower" and bull2 and doji1 and gap_up and bear0 and gap_dn_out:
            score += 2.4
            reasons.append("bearish abandoned-baby")

        # Tri-star (three dojis) — rare; mild
        if (
            doji0
            and doji1
            and _is_doji(o2, h2, l2, c2)
        ):
            if direction == "upper" and closes[-1] > closes[-3]:
                score += 1.2
                reasons.append("bullish tri-star")
            if direction == "lower" and closes[-1] < closes[-3]:
                score += 1.2
                reasons.append("bearish tri-star")

    # --- Four / five candle ---
    if n >= 4:
        # Rising / falling three methods (trend continuation)
        o3, c3 = opens[-4], closes[-4]
        bull3, bear3 = _is_bull(o3, c3), _is_bear(o3, c3)
        # Big bull, 2 small inside, then bull close
        if (
            direction == "upper"
            and bull3
            and _body(o3, c3) > _body(opens[-3], closes[-3])
            and _body(o3, c3) > _body(opens[-2], closes[-2])
            and highs[-3] < highs[-4]
            and lows[-3] > lows[-4]
            and highs[-2] < highs[-4]
            and lows[-2] > lows[-4]
            and bull0
            and c0 > c3
        ):
            score += 1.6
            reasons.append("rising-three-methods")
        if (
            direction == "lower"
            and bear3
            and _body(o3, c3) > _body(opens[-3], closes[-3])
            and _body(o3, c3) > _body(opens[-2], closes[-2])
            and highs[-3] < highs[-4]
            and lows[-3] > lows[-4]
            and highs[-2] < highs[-4]
            and lows[-2] > lows[-4]
            and bear0
            and c0 < c3
        ):
            score += 1.6
            reasons.append("falling-three-methods")

    if n >= 5:
        # Mat hold (continuation) — simplified
        if (
            direction == "upper"
            and _is_bull(opens[-5], closes[-5])
            and closes[-1] > max(closes[-5:-1])
            and _is_bull(o0, c0)
        ):
            pullback = all(closes[i] <= closes[-5] for i in range(-4, -1))
            if pullback:
                score += 1.3
                reasons.append("bullish mat-hold-like")
        if (
            direction == "lower"
            and _is_bear(opens[-5], closes[-5])
            and closes[-1] < min(closes[-5:-1])
            and _is_bear(o0, c0)
        ):
            pullback = all(closes[i] >= closes[-5] for i in range(-4, -1))
            if pullback:
                score += 1.3
                reasons.append("bearish mat-hold-like")

    return score, reasons


def _score_tf(candles: list[dict], direction: str) -> tuple[float, list[str]]:
    """Score one timeframe; direction is upper|lower."""
    if len(candles) < 8:
        return 0.0, ["insufficient candles"]

    closes = [float(c["close"]) for c in candles]
    opens = [float(c["open"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    score = 0.0
    reasons: list[str] = []

    # Last completed-ish candle body (use last candle)
    last_bull = closes[-1] > opens[-1]
    last_bear = closes[-1] < opens[-1]
    if direction == "upper" and last_bull:
        score += 1.0
        reasons.append("last candle bullish")
    elif direction == "lower" and last_bear:
        score += 1.0
        reasons.append("last candle bearish")

    # Recent streak (last 3)
    bodies = [1 if c > o else (-1 if c < o else 0) for o, c in zip(opens[-3:], closes[-3:])]
    if direction == "upper" and sum(1 for b in bodies if b > 0) >= 2:
        score += 1.5
        reasons.append("2/3 recent bullish")
    elif direction == "lower" and sum(1 for b in bodies if b < 0) >= 2:
        score += 1.5
        reasons.append("2/3 recent bearish")

    # EMA trend on closes
    ema_fast = _ema(closes, 8)
    ema_slow = _ema(closes, 21)
    if ema_fast and ema_slow:
        if direction == "upper" and ema_fast[-1] > ema_slow[-1] and ema_fast[-1] >= ema_fast[-2]:
            score += 2.0
            reasons.append("EMA8>EMA21 rising")
        elif direction == "lower" and ema_fast[-1] < ema_slow[-1] and ema_fast[-1] <= ema_fast[-2]:
            score += 2.0
            reasons.append("EMA8<EMA21 falling")

    # Structure: higher highs/lows or lower highs/lows
    if len(highs) >= 6:
        hh = highs[-1] > highs[-3] and lows[-1] >= lows[-3]
        ll = lows[-1] < lows[-3] and highs[-1] <= highs[-3]
        if direction == "upper" and hh:
            score += 1.5
            reasons.append("higher-high structure")
        elif direction == "lower" and ll:
            score += 1.5
            reasons.append("lower-low structure")

    rsi = _rsi_last(closes, 14)
    if direction == "upper" and 45 <= rsi <= 72:
        score += 1.0
        reasons.append(f"RSI supportive ({rsi:.0f})")
    elif direction == "lower" and 28 <= rsi <= 55:
        score += 1.0
        reasons.append(f"RSI supportive ({rsi:.0f})")

    # Classic candle patterns
    p_score, p_reasons = _score_patterns(opens, highs, lows, closes, direction)
    score += p_score
    reasons.extend(p_reasons)

    return score, reasons


async def fetch_deriv_official_candles(client: Any, symbol: str) -> dict[str, list[dict]]:
    """
    Pull official Deriv OHLC for 1m / 5m / 15m on the public WS client.
    """
    c1 = await client.get_candles(symbol, granularity=60, count=80)
    c5 = await client.get_candles(symbol, granularity=300, count=40)
    c15 = await client.get_candles(symbol, granularity=900, count=24)
    if len(c1) < 8 or len(c5) < 8:
        raise RuntimeError(
            f"Official candles too short: 1m={len(c1)} 5m={len(c5)} 15m={len(c15)}"
        )
    return {"1m": c1, "5m": c5, "15m": c15, "source": "deriv_official"}


def _drop_forming_candle(candles: list[dict], *, min_keep: int = 8) -> list[dict]:
    """Prefer completed bars only — the latest candle is often still forming."""
    if len(candles) <= min_keep:
        return candles
    return candles[:-1]


def evaluate_candle_confirm_ohlc(
    candles_1m: list[dict],
    candles_5m: list[dict],
    candles_15m: list[dict],
    direction: str,
    *,
    min_score: float = 4.0,
    min_gap: float = 1.0,
    source: str = "ohlc",
) -> CandleConfirmResult:
    """Confirm direction from pre-built 1m/5m/15m OHLC lists."""
    direction = str(direction).lower()
    if direction not in ("upper", "lower"):
        return CandleConfirmResult(
            False, direction, 0.0, 0.0, "invalid direction", {}
        )

    # Official OHLC is cleaner than tick buckets — use completed bars + stricter bar
    is_official = str(source) == "deriv_official"
    c1 = _drop_forming_candle(candles_1m) if is_official else list(candles_1m)
    c5 = _drop_forming_candle(candles_5m) if is_official else list(candles_5m)
    c15 = _drop_forming_candle(candles_15m) if is_official else list(candles_15m)
    need_score = float(min_score) + (1.5 if is_official else 0.0)
    need_gap = float(min_gap) + (0.5 if is_official else 0.0)

    if len(c1) < 8 or len(c5) < 8:
        return CandleConfirmResult(
            False,
            direction,
            0.0,
            0.0,
            "insufficient official/local candles",
            {"n_1m": len(c1), "n_5m": len(c5), "n_15m": len(c15), "source": source},
        )

    opposite = "lower" if direction == "upper" else "upper"
    s1, r1 = _score_tf(c1, direction)
    s5, r5 = _score_tf(c5, direction)
    s15, r15 = _score_tf(c15, direction)
    o1, _ = _score_tf(c1, opposite)
    o5, _ = _score_tf(c5, opposite)
    o15, _ = _score_tf(c15, opposite)

    score = 0.30 * s1 + 0.45 * s5 + 0.25 * s15
    opp = 0.30 * o1 + 0.45 * o5 + 0.25 * o15
    gap = score - opp
    confirmed = score >= need_score and gap >= need_gap

    reasons = (
        [f"1m:{x}" for x in r1[:2]]
        + [f"5m:{x}" for x in r5[:3]]
        + [f"15m:{x}" for x in r15[:2]]
    )
    src_label = "official" if is_official else source
    expl = (
        f"candle_confirm[{src_label}] {direction}: score={score:.1f} "
        f"(opp={opp:.1f}, gap={gap:.1f}) need≥{need_score:.1f}/{need_gap:.1f} — "
        + ("; ".join(reasons) if reasons else "weak structure")
    )
    return CandleConfirmResult(
        confirmed=confirmed,
        direction=direction,
        score=float(score),
        opposite_score=float(opp),
        explanation=expl,
        details={
            "score_1m": s1,
            "score_5m": s5,
            "score_15m": s15,
            "n_1m": len(c1),
            "n_5m": len(c5),
            "n_15m": len(c15),
            "min_score": min_score,
            "min_gap": min_gap,
            "effective_min_score": need_score,
            "effective_min_gap": need_gap,
            "reasons": reasons,
            "source": source,
        },
    )


def evaluate_candle_confirm(
    ticks: Iterable[tuple[int, float]] | pd.DataFrame,
    direction: str,
    *,
    min_score: float = 4.0,
    min_gap: float = 1.0,
) -> CandleConfirmResult:
    """
    Fallback: confirm direction using 1m/5m/15m candles built from ticks.
    Prefer evaluate_candle_confirm_ohlc with Deriv official candles in live paths.
    """
    direction = str(direction).lower()
    if direction not in ("upper", "lower"):
        return CandleConfirmResult(
            False, direction, 0.0, 0.0, "invalid direction", {}
        )

    if isinstance(ticks, pd.DataFrame):
        if ticks.empty or "epoch" not in ticks.columns or "quote" not in ticks.columns:
            return CandleConfirmResult(
                False, direction, 0.0, 0.0, "no tick columns for candles", {}
            )
        pairs = list(zip(ticks["epoch"].astype(int), ticks["quote"].astype(float)))
    else:
        pairs = [(int(e), float(q)) for e, q in ticks]

    if len(pairs) < 80:
        return CandleConfirmResult(
            False, direction, 0.0, 0.0, "need more ticks for candle confirm", {"n_ticks": len(pairs)}
        )

    c1 = aggregate_ohlc(pairs, "1m", max_candles=80)
    c5 = aggregate_ohlc(pairs, "5m", max_candles=40)
    c15 = aggregate_ohlc(pairs, "15m", max_candles=24)
    return evaluate_candle_confirm_ohlc(
        c1, c5, c15, direction, min_score=min_score, min_gap=min_gap, source="tick_aggregate"
    )


def evaluate_candle_confirm_best(
    direction: str,
    *,
    official: Optional[dict[str, list[dict]]] = None,
    ticks: Iterable[tuple[int, float]] | pd.DataFrame | None = None,
    min_score: float = 4.0,
    min_gap: float = 1.0,
) -> CandleConfirmResult:
    """Use official Deriv OHLC when present; else tick-aggregated fallback."""
    if official and official.get("1m") and official.get("5m"):
        return evaluate_candle_confirm_ohlc(
            official["1m"],
            official.get("5m") or [],
            official.get("15m") or [],
            direction,
            min_score=min_score,
            min_gap=min_gap,
            source=str(official.get("source") or "deriv_official"),
        )
    if ticks is not None:
        return evaluate_candle_confirm(
            ticks, direction, min_score=min_score, min_gap=min_gap
        )
    return CandleConfirmResult(
        False, str(direction), 0.0, 0.0, "no official candles or ticks", {}
    )


def ticks_from_features(features_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Extract epoch/quote for candle building from a feature frame if present."""
    if features_df is None or features_df.empty:
        return None
    if "epoch" in features_df.columns and "quote" in features_df.columns:
        return features_df[["epoch", "quote"]].dropna()
    return None
