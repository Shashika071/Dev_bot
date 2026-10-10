"""Historical evaluation. Assumed payouts are never reported as demo results."""

from __future__ import annotations

import numpy as np

from app.digitmatch.probability import as_simplex, log_loss_safe, multiclass_brier


def top_hit_rate(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    probs = as_simplex(probabilities)
    choice = probs.argmax(axis=1)
    y = np.asarray(y_true, dtype=int)
    if len(y) == 0:
        return float("nan")
    return float(np.mean(choice == y))


def per_digit(y_true: np.ndarray, probabilities: np.ndarray) -> list[dict]:
    probs = as_simplex(probabilities)
    y = np.asarray(y_true, dtype=int)
    choice = probs.argmax(axis=1)
    rows = []
    for digit in range(10):
        mask = y == digit
        chosen = choice == digit
        support = int(mask.sum())
        rows.append(
            {
                "digit": digit,
                "support": support,
                "hit_rate_when_true": float(np.mean(choice[mask] == digit)) if support else None,
                "times_selected": int(chosen.sum()),
                "precision_when_selected": float(np.mean(y[chosen] == digit)) if chosen.any() else None,
            }
        )
    return rows


def calibration_bins(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> list[dict]:
    """Reliability of the top-choice probability. Empty when there are no rows."""
    probs = as_simplex(probabilities)
    y = np.asarray(y_true, dtype=int)
    confidence = probs.max(axis=1)
    correct = (probs.argmax(axis=1) == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        mask = (confidence >= edges[i]) & (confidence < edges[i + 1] if i < bins - 1 else confidence <= edges[i + 1])
        count = int(mask.sum())
        out.append(
            {
                "bin_start": float(edges[i]),
                "bin_end": float(edges[i + 1]),
                "count": count,
                "mean_confidence": float(confidence[mask].mean()) if count else None,
                "empirical_hit_rate": float(correct[mask].mean()) if count else None,
            }
        )
    return out


def block_bootstrap_mean(values: np.ndarray, *, block: int = 20, draws: int = 200, seed: int = 0) -> dict:
    """Mean and percentile interval using contiguous blocks, not iid draws."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return {"mean": None, "low": None, "high": None, "block": block, "draws": 0}
    rng = np.random.default_rng(seed)
    block = max(1, min(block, len(values)))
    n_blocks = int(np.ceil(len(values) / block))
    starts = np.arange(0, len(values) - block + 1)
    means = []
    for _ in range(draws):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        sample = np.concatenate([values[s : s + block] for s in chosen])[: len(values)]
        means.append(float(sample.mean()))
    arr = np.asarray(means)
    return {
        "mean": float(values.mean()),
        "low": float(np.quantile(arr, 0.025)),
        "high": float(np.quantile(arr, 0.975)),
        "block": block,
        "draws": draws,
    }


def max_drawdown(equity: np.ndarray) -> float | None:
    if len(equity) == 0:
        return None
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    return float(drawdown.max())


def longest_losing_streak(wins: np.ndarray) -> int:
    streak = best = 0
    for win in wins:
        if not win:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return int(best)


def period_table(epochs: np.ndarray, correct: np.ndarray, bucket: int = 500) -> list[dict]:
    if len(epochs) == 0:
        return []
    epochs = np.asarray(epochs)
    correct = np.asarray(correct, dtype=bool)
    start = int(epochs.min())
    rows = []
    left = start
    while left <= int(epochs.max()):
        right = left + bucket
        mask = (epochs >= left) & (epochs < right)
        count = int(mask.sum())
        rows.append(
            {
                "start_epoch": left,
                "end_epoch": right,
                "count": count,
                "top_hit_rate": float(correct[mask].mean()) if count else None,
            }
        )
        left = right
    return rows


def evaluate_predictions(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    *,
    epochs: np.ndarray | None = None,
    block: int = 20,
    seed: int = 0,
) -> dict:
    probs = as_simplex(probabilities)
    y = np.asarray(y_true, dtype=int)
    point_loss = -np.log(np.clip(probs[np.arange(len(y)), y], 1e-15, 1))
    uniform = np.full_like(probs, 0.1)
    uniform_point = -np.log(np.clip(uniform[np.arange(len(y)), y], 1e-15, 1))
    choice = probs.argmax(axis=1)
    return {
        "count": int(len(y)),
        "log_loss": log_loss_safe(y, probs),
        "brier": multiclass_brier(y, probs),
        "uniform_log_loss": log_loss_safe(y, uniform),
        "log_loss_minus_uniform": block_bootstrap_mean(point_loss - uniform_point, block=block, seed=seed),
        "top_hit_rate": top_hit_rate(y, probs),
        "per_digit": per_digit(y, probs),
        "calibration_bins": calibration_bins(y, probs),
        "by_period": period_table(epochs if epochs is not None else np.arange(len(y)), choice == y),
        "selected_trades": None,
        "selected_trades_reason": (
            "Historical Digit Matches payout quotes were not in the tick archive. "
            "Selected-trade return is omitted. No payout was assumed."
        ),
        "payout_adjusted_return": None,
        "result_kind": "historical_prediction_evaluation",
    }


def simulate_assumed_payout(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    *,
    ask: float,
    total_payout: float,
    margin: float,
) -> dict:
    """Explicitly hypothetical. Not a demo-trading result."""
    probs = as_simplex(probabilities)
    y = np.asarray(y_true, dtype=int)
    choice = probs.argmax(axis=1)
    p_choice = probs[np.arange(len(y)), choice]
    break_even = ask / total_payout
    take = p_choice >= break_even + margin
    wins = (choice == y) & take
    pnl = np.zeros(len(y))
    pnl[take & (choice == y)] = total_payout - ask
    pnl[take & (choice != y)] = -ask
    equity = np.cumsum(pnl)
    return {
        "result_kind": "simulated_returns_under_assumed_payout",
        "not_actual_trading": True,
        "assumed_ask": ask,
        "assumed_total_payout": total_payout,
        "margin": margin,
        "selected_trade_count": int(take.sum()),
        "selected_win_rate": float(wins[take].mean()) if take.any() else None,
        "payout_adjusted_return": float(pnl.sum()),
        "max_drawdown": max_drawdown(equity),
        "longest_losing_streak": longest_losing_streak(choice[take] == y[take]) if take.any() else 0,
    }
