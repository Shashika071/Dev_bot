"""Metrics, calibration summaries, payout math, and report text.

No operating point is fixed at 70 percent. Thresholds come from validation
probabilities, payout economics, and block-bootstrap uncertainty.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score


def expected_return_per_stake(probability: float, net_profit_r: float) -> float:
    """p * r - (1 - p), for a contract that loses 1 stake or earns r stakes."""
    p = float(probability)
    r = float(net_profit_r)
    return p * r - (1.0 - p)


def breakeven_probability(net_profit_r: float) -> float:
    return 1.0 / (1.0 + float(net_profit_r))


def binary_logloss(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1.0 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def brier_score(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((p - y) ** 2))


def safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    y = np.asarray(y)
    if len(y) == 0 or len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, p))


def block_bootstrap_mean(
    values: np.ndarray,
    block: int,
    n_boot: int = 400,
    seed: int = 42,
) -> dict:
    """Resample contiguous blocks so overlapping labels are not treated as independent."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return {"mean": None, "ci90_low": None, "ci90_high": None, "block_length": int(block), "n": 0}
    block = max(1, min(int(block), n))
    rng = np.random.default_rng(seed)
    starts = np.arange(0, n - block + 1)
    n_blocks = int(np.ceil(n / block))
    means = np.empty(n_boot)
    for i in range(n_boot):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        sample = np.concatenate([values[s : s + block] for s in chosen])[:n]
        means[i] = float(sample.mean())
    return {
        "mean": float(values.mean()),
        "bootstrap_mean": float(means.mean()),
        "ci90_low": float(np.quantile(means, 0.05)),
        "ci90_high": float(np.quantile(means, 0.95)),
        "block_length": int(block),
        "n": int(n),
        "n_bootstrap": int(n_boot),
    }


def calibration_bins(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for i in range(n_bins):
        if i == n_bins - 1:
            mask = (p >= edges[i]) & (p <= edges[i + 1])
        else:
            mask = (p >= edges[i]) & (p < edges[i + 1])
        if int(mask.sum()) == 0:
            continue
        rows.append(
            {
                "bin_low": float(edges[i]),
                "bin_high": float(edges[i + 1]),
                "count": int(mask.sum()),
                "mean_predicted": float(p[mask].mean()),
                "observed_frequency": float(y[mask].mean()),
            }
        )
    return rows


def write_calibration_svg(path: Path, bins: list[dict]) -> None:
    width, height, pad = 420, 420, 40
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{pad}" stroke="#bbbbbb"/>',
        f'<text x="{pad}" y="24" font-size="14">Calibration (predicted vs observed)</text>',
    ]
    plot = width - 2 * pad

    def xy(pred: float, obs: float) -> tuple[float, float]:
        return pad + pred * plot, height - pad - obs * plot

    if bins:
        points = [xy(row["mean_predicted"], row["observed_frequency"]) for row in bins]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        parts.append(f'<polyline fill="none" stroke="#1f4b99" stroke-width="2" points="{poly}"/>')
        for row, (x, y) in zip(bins, points):
            radius = 3 + min(8, row["count"] ** 0.5)
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="#1f4b99"/>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def operating_point(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    y = np.asarray(y).astype(int)
    pred = np.asarray(p) >= float(threshold)
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    tn = int(np.sum(~pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    return {
        "threshold": float(threshold),
        "precision": (tp / (tp + fp)) if (tp + fp) else None,
        "recall": (tp / (tp + fn)) if (tp + fn) else None,
        "coverage": float(np.mean(pred)) if len(pred) else None,
        "signals": int(pred.sum()),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def metrics_block(
    y: np.ndarray,
    p: np.ndarray,
    *,
    block: int,
    n_boot: int,
    seed: int,
    threshold: float | None,
    net_profit_r: float | None,
) -> dict:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    losses = _row_logloss(y, p)
    out = {
        "rows": int(len(y)),
        "outcome_frequency": float(y.mean()) if len(y) else None,
        "logloss": binary_logloss(y, p) if len(y) else None,
        "brier": brier_score(y, p) if len(y) else None,
        "roc_auc": safe_auc(y, p),
        "logloss_block_bootstrap": block_bootstrap_mean(losses, block, n_boot, seed),
        "calibration_bins": calibration_bins(y, p),
        "threshold": None,
        "operating_point": None,
    }
    if threshold is not None and len(y):
        point = operating_point(y, p, threshold)
        out["threshold"] = float(threshold)
        out["operating_point"] = point
        if net_profit_r is not None:
            pnl = np.zeros(len(y))
            signaled = p >= threshold
            pnl[signaled] = np.where(y[signaled] == 1, float(net_profit_r), -1.0)
            out["hypothetical_pnl_per_opportunity"] = block_bootstrap_mean(pnl, block, n_boot, seed)
            wins = y[signaled] == 1
            out["signaled_outcome_frequency"] = float(y[signaled].mean()) if signaled.any() else None
            out["signals"] = int(signaled.sum())
            if signaled.any():
                out["note_on_wins"] = int(wins.sum())
    return out


def by_period(epochs: np.ndarray, y: np.ndarray, p: np.ndarray, n_bins: int = 4) -> list[dict]:
    epochs = np.asarray(epochs, dtype=float)
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(epochs) < n_bins:
        return []
    edges = np.quantile(epochs, np.linspace(0, 1, n_bins + 1))
    rows = []
    for i in range(n_bins):
        if i == n_bins - 1:
            mask = (epochs >= edges[i]) & (epochs <= edges[i + 1])
        else:
            mask = (epochs >= edges[i]) & (epochs < edges[i + 1])
        if int(mask.sum()) < 2:
            continue
        rows.append(
            {
                "start_epoch": int(epochs[mask].min()),
                "end_epoch": int(epochs[mask].max()),
                "rows": int(mask.sum()),
                "outcome_frequency": float(y[mask].mean()),
                "logloss": binary_logloss(y[mask], p[mask]),
                "roc_auc": safe_auc(y[mask], p[mask]),
            }
        )
    return rows


def approximate_independent_count(epochs: np.ndarray, horizon_seconds: int) -> int:
    epochs = np.asarray(epochs, dtype=float)
    if len(epochs) == 0:
        return 0
    if len(epochs) == 1:
        return 1
    span = float(epochs.max() - epochs.min())
    return int(span // int(horizon_seconds)) + 1


def select_threshold(
    y: np.ndarray,
    p: np.ndarray,
    net_profit_r: float | None,
    block: int,
    n_boot: int,
    seed: int,
) -> dict:
    """Choose a threshold on validation rows only.

    Without a payout multiple there is no economic threshold. The function
    does not fall back to 0.70.
    """
    if net_profit_r is None:
        return {
            "threshold": None,
            "reason": "no historical quote and no hypothetical payout; probability is reported without a trade threshold",
            "breakeven_probability": None,
        }
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    breakeven = breakeven_probability(net_profit_r)
    grid = np.unique(np.quantile(p, np.linspace(0.05, 0.99, 25)))
    best = None
    for threshold in grid:
        if float(threshold) + 1e-12 < breakeven:
            continue
        signaled = p >= threshold
        if int(signaled.sum()) < 5:
            continue
        pnl = np.zeros(len(y))
        pnl[signaled] = np.where(y[signaled] == 1, float(net_profit_r), -1.0)
        stats = block_bootstrap_mean(pnl, block, n_boot, seed)
        if stats["ci90_low"] is None or stats["ci90_low"] <= 0:
            continue
        if best is None or stats["mean"] > best["validation_mean_pnl"]:
            best = {
                "threshold": float(threshold),
                "validation_mean_pnl": stats["mean"],
                "validation_ci90_low": stats["ci90_low"],
                "validation_ci90_high": stats["ci90_high"],
                "validation_signals": int(signaled.sum()),
            }
    if best is None:
        return {
            "threshold": None,
            "breakeven_probability": breakeven,
            "net_profit_r": float(net_profit_r),
            "reason": (
                "no validation threshold at or above the break-even probability had a "
                "positive block-bootstrap lower bound"
            ),
        }
    best["breakeven_probability"] = breakeven
    best["net_profit_r"] = float(net_profit_r)
    best["reason"] = "selected on chronological validation outcomes and block-bootstrap uncertainty"
    return best


def choose_feature_group(fold_losses: dict[str, list[float]]) -> dict:
    """Keep a richer group only when its log loss is lower on a majority of folds."""
    order = ["returns_vol", "indicators", "strategy"]
    selected = "returns_vol"
    comparisons = []
    for richer, base in (("indicators", "returns_vol"), ("strategy", "indicators")):
        base_losses = fold_losses.get(base) or []
        rich_losses = fold_losses.get(richer) or []
        n = min(len(base_losses), len(rich_losses))
        if n == 0:
            comparisons.append({"richer": richer, "base": base, "kept": False, "reason": "no folds"})
            continue
        wins = sum(1 for i in range(n) if rich_losses[i] < base_losses[i] - 1e-4)
        mean_rich = float(np.mean(rich_losses[:n]))
        mean_base = float(np.mean(base_losses[:n]))
        keep = wins > (n / 2) and mean_rich < mean_base
        comparisons.append(
            {
                "richer": richer,
                "base": base,
                "folds": n,
                "folds_improved": int(wins),
                "mean_logloss_richer": mean_rich,
                "mean_logloss_base": mean_base,
                "kept": bool(keep),
            }
        )
        if selected == base and keep:
            selected = richer
    return {"selected_group": selected, "comparisons": comparisons, "candidate_order": order}


def equal_weight_beats_best(y: np.ndarray, model_probs: dict[str, np.ndarray]) -> dict:
    y = np.asarray(y, dtype=float)
    losses = {name: binary_logloss(y, probs) for name, probs in model_probs.items()}
    if not losses:
        return {"kept": False, "reason": "no models"}
    best_name = min(losses, key=losses.get)
    if len(model_probs) < 2:
        return {"kept": False, "best_single": best_name, "losses": losses, "reason": "only one model"}
    stacked = np.vstack([model_probs[name] for name in model_probs])
    average = stacked.mean(axis=0)
    avg_loss = binary_logloss(y, average)
    kept = avg_loss + 1e-6 < losses[best_name]
    return {
        "kept": bool(kept),
        "best_single": best_name,
        "best_single_logloss": losses[best_name],
        "ensemble_logloss": avg_loss,
        "losses": losses,
        "reason": "equal-weight calibrated average improved validation log loss"
        if kept
        else "equal-weight average did not improve validation log loss",
    }


def _row_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1.0 - 1e-6)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# Research evaluation",
        "",
        report.get("banner", ""),
        "",
        "This file reports predictive checks. It is not a verified profitability result",
        "unless a historical quote section says quotes were matched. No order is placed.",
        "",
    ]
    quality = report.get("data_quality") or {}
    lines.append("## Data")
    lines.append("")
    for key in (
        "status",
        "instrument",
        "tick_count",
        "start_utc",
        "end_utc",
        "span_hours",
        "median_tick_interval_seconds",
        "timestamp_input",
        "timezone",
        "true_duplicate_rows_removed",
        "conflicting_same_epoch_rows_removed",
        "invalid_price_rows_removed",
        "missing_interval_count",
        "longest_gap_seconds",
    ):
        if key in quality:
            lines.append(f"- {key}: {quality[key]}")
    lines.append("")
    lines.append("## Targets")
    lines.append("")
    for name, body in (report.get("targets") or {}).items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- test opened: {body.get('test_opened')}")
        lines.append(f"- labeled examples: {body.get('labeled_examples')}")
        lines.append(f"- approximate independent outcomes: {body.get('approximate_independent_outcomes')}")
        lines.append(f"- selected group: {(body.get('selection') or {}).get('feature_group')}")
        lines.append(f"- selected model: {(body.get('selection') or {}).get('model')}")
        test = body.get("test_metrics") or {}
        if test:
            lines.append(f"- test outcome frequency: {test.get('outcome_frequency')}")
            lines.append(f"- test log loss: {test.get('logloss')}")
            lines.append(f"- test Brier: {test.get('brier')}")
            lines.append(f"- test ROC AUC: {test.get('roc_auc')}")
            base = (body.get("selection") or {}).get("baseline_logloss")
            lines.append(f"- validation constant-baseline log loss: {base}")
        if body.get("limitation"):
            lines.append(f"- limitation: {body['limitation']}")
        lines.append("")
    lines.append("## Unresolved before a final contract test")
    lines.append("")
    for item in report.get("unresolved_settings") or []:
        lines.append(f"- {item}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
