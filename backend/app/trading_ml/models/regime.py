"""Market regime from closed-candle features, plus an optional volatility HMM."""
from __future__ import annotations

import math

import numpy as np


def regime_name(row) -> str:
    try:
        adx = float(row["adx14"])
        ratio = float(row["rv_ratio"])
        agreement = float(row["mtf_agreement"])
    except (KeyError, TypeError, ValueError):
        return "unknown"
    if not all(math.isfinite(value) for value in (adx, ratio, agreement)):
        return "unknown"
    if ratio >= 1.25:
        volatility = "high_volatility"
    elif ratio <= 0.8:
        volatility = "low_volatility"
    else:
        volatility = "normal_volatility"
    if adx >= 25 and abs(agreement) >= 0.5:
        structure = "trending"
    elif adx <= 18:
        structure = "ranging"
    else:
        structure = "transition"
    return f"{structure}_{volatility}"


def fit_volatility_hmm(returns: np.ndarray) -> dict | None:
    """Two-state Gaussian HMM. Emissions come from the training returns only."""
    values = np.asarray(returns, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 50:
        return None
    energy = values ** 2
    low = max(float(np.quantile(energy, 0.30)), 1e-12)
    high = max(float(np.quantile(energy, 0.70)), low * 1.5)
    return {
        "variance": [low, high],
        "transition": [[0.95, 0.05], [0.05, 0.95]],
        "start": [0.5, 0.5],
        "method": "two_state_gaussian_hmm",
    }


def filter_hmm_state(model: dict, returns: np.ndarray) -> int | None:
    """Forward filter. The state at t uses returns through t."""
    values = np.asarray(returns, dtype=float)
    if model is None or not len(values) or not np.isfinite(values).all():
        return None
    variance = np.asarray(model["variance"], dtype=float)
    transition = np.asarray(model["transition"], dtype=float)
    scale = np.asarray(model["start"], dtype=float)
    last = None
    for value in values:
        emission = np.exp(-0.5 * (np.log(2 * math.pi * variance) + (value ** 2) / variance))
        scale = emission * (scale @ transition)
        total = float(scale.sum())
        if total <= 0 or not math.isfinite(total):
            return None
        scale = scale / total
        last = int(np.argmax(scale))
    return last


def hmm_report(train_returns: np.ndarray, live_returns: np.ndarray) -> dict:
    model = fit_volatility_hmm(train_returns)
    if model is None:
        return {
            "available": False, "used_for_signal": False, "state": None,
            "reason": "Not enough training returns for the optional volatility HMM.",
        }
    state = filter_hmm_state(model, live_returns)
    return {
        "available": state is not None,
        "used_for_signal": False,
        "state": None if state is None else ("high_volatility" if state == 1 else "low_volatility"),
        "method": model["method"],
        "reason": "HMM state is research context. The signal uses the rule-based regime.",
    }
