"""Shared feature matrix for training and live inference.

Every value at row t uses only ticks at indexes <= t.
"""

from __future__ import annotations

import math

import numpy as np

from app.digitmatch import FEATURE_SCHEMA
from app.digitmatch.validation import window_is_clean

HORIZON = 5
LOOKBACK = 1000


def feature_names() -> list[str]:
    names: list[str] = []
    for lag in range(10):
        for digit in range(10):
            names.append(f"prev{lag}_d{digit}")
    for window in (20, 30, 50, 200, 1000):
        for digit in range(10):
            names.append(f"freq{window}_d{digit}")
    names.append("repetition")
    for digit in range(10):
        names.append(f"since_d{digit}")
    for digit in range(10):
        names.append(f"trans_d{digit}")
    names.extend(["ret1", "ret5", "ret10", "vol20", "vol50", "ent50", "ent200"])
    return names


def _entropy(row: np.ndarray) -> float:
    total = float(row.sum())
    if total <= 0:
        return 0.0
    probs = row / total
    probs = probs[probs > 0]
    return float(-(probs * np.log(probs)).sum())


def build_examples(
    digits: np.ndarray,
    prices: np.ndarray,
    usable: np.ndarray,
    *,
    horizon: int = HORIZON,
    lookback: int = LOOKBACK,
) -> dict:
    """Research examples. Target is the digit `horizon` ticks ahead."""
    digits = np.asarray(digits, dtype=int)
    prices = np.asarray(prices, dtype=float)
    usable = np.asarray(usable, dtype=bool)
    if digits.ndim != 1 or len(digits) != len(prices) or len(digits) != len(usable):
        raise ValueError("digits, prices, and usable must be aligned 1-d arrays")
    names = feature_names()
    n = len(digits)
    rows: list[np.ndarray] = []
    targets: list[int] = []
    indexes: list[int] = []
    if n <= lookback + horizon:
        return {
            "schema": FEATURE_SCHEMA,
            "names": names,
            "X": np.zeros((0, len(names))),
            "y": np.zeros((0,), dtype=int),
            "index": np.zeros((0,), dtype=int),
            "horizon": horizon,
            "target_note": "research_proxy_digit_at_t_plus_horizon",
        }

    one_hot = np.eye(10, dtype=float)[digits]
    cumulative = np.cumsum(one_hot, axis=0)
    pair = digits[:-1] * 10 + digits[1:]
    pair_hot = np.eye(100, dtype=float)[pair]
    pair_cum = np.cumsum(pair_hot, axis=0)

    last_seen = np.full(10, -1, dtype=int)
    since = np.zeros((n, 10), dtype=float)
    repetition = np.ones(n, dtype=float)
    for i, digit in enumerate(digits):
        for d in range(10):
            since[i, d] = 0.0 if d == digit else (i - last_seen[d] if last_seen[d] >= 0 else float(i + 1))
        last_seen[digit] = i
        if i:
            repetition[i] = repetition[i - 1] + 1 if digits[i] == digits[i - 1] else 1.0

    returns = np.zeros(n, dtype=float)
    nz = prices[:-1] != 0
    returns[1:][nz] = np.diff(prices)[nz] / prices[:-1][nz]

    for i in range(lookback - 1, n - horizon):
        if not window_is_clean(usable.tolist(), i - (lookback - 1), i + horizon):
            continue
        parts: list[float] = []
        for lag in range(10):
            parts.extend(one_hot[i - lag].tolist())
        for window in (20, 30, 50, 200, 1000):
            end = cumulative[i]
            start = cumulative[i - window] if i - window >= 0 else 0
            counts = end - start
            parts.extend((counts / window).tolist())
        parts.append(float(repetition[i]))
        parts.extend(since[i].tolist())
        # Transitions that have already happened: pairs ending at i.
        end_pair = i - 1
        start_pair = end_pair - 200
        if end_pair < 0:
            counts = np.zeros(100)
        else:
            counts = pair_cum[end_pair] - (pair_cum[start_pair] if start_pair >= 0 else 0)
        row = counts[digits[i] * 10 : (digits[i] + 1) * 10]
        smoothed = (row + 1.0) / (row.sum() + 10.0)
        parts.extend(smoothed.tolist())
        for lag in (1, 5, 10):
            base = prices[i - lag]
            parts.append(float((prices[i] - base) / base) if base else 0.0)
        parts.append(float(returns[i - 19 : i + 1].std(ddof=0)))
        parts.append(float(returns[i - 49 : i + 1].std(ddof=0)))
        freq50 = cumulative[i] - cumulative[i - 50]
        freq200 = cumulative[i] - cumulative[i - 200]
        parts.append(_entropy(freq50))
        parts.append(_entropy(freq200))
        vector = np.asarray(parts, dtype=float)
        if not np.isfinite(vector).all():
            continue
        rows.append(vector)
        targets.append(int(digits[i + horizon]))
        indexes.append(i)

    x = np.vstack(rows) if rows else np.zeros((0, len(names)))
    return {
        "schema": FEATURE_SCHEMA,
        "names": names,
        "X": x,
        "y": np.asarray(targets, dtype=int),
        "index": np.asarray(indexes, dtype=int),
        "horizon": horizon,
        "target_note": "research_proxy_digit_at_t_plus_horizon",
    }


def live_feature_row(digits: np.ndarray, prices: np.ndarray, usable: np.ndarray) -> np.ndarray | None:
    """Feature row for the latest tick, or None while the window is incomplete."""
    built = build_examples(digits, prices, usable, horizon=0)
    # horizon 0 would include a target at t. Rebuild the last row directly.
    del built
    digits = np.asarray(digits, dtype=int)
    prices = np.asarray(prices, dtype=float)
    usable = np.asarray(usable, dtype=bool)
    if len(digits) < LOOKBACK:
        return None
    i = len(digits) - 1
    if not window_is_clean(usable.tolist(), i - (LOOKBACK - 1), i):
        return None
    probe_digits = np.concatenate([digits, np.zeros(HORIZON, dtype=int)])
    probe_prices = np.concatenate([prices, np.repeat(prices[-1], HORIZON)])
    probe_usable = np.concatenate([usable, np.ones(HORIZON, dtype=bool)])
    # The probe future is excluded by computing the row at i via a copy of the
    # training function's index filter: ask for horizon examples whose index == i.
    examples = build_examples(probe_digits, probe_prices, probe_usable)
    if len(examples["index"]) == 0 or int(examples["index"][-1]) != i:
        return None
    # Guard: the probe digits must not affect the row. Compare against a
    # different probe; both rows for index i must match.
    alt = probe_digits.copy()
    alt[-HORIZON:] = (alt[-HORIZON:] + 3) % 10
    other = build_examples(alt, probe_prices, probe_usable)
    if len(other["index"]) == 0 or int(other["index"][-1]) != i:
        return None
    if not np.allclose(examples["X"][-1], other["X"][-1]):
        raise RuntimeError("live feature row changed when future digits changed")
    return examples["X"][-1]


def entropy_is_finite(value: float) -> bool:
    return math.isfinite(value)
