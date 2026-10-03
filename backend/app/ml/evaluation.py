"""
Model evaluation — comprehensive metrics for touch prediction models.

Reports calibration, win rate, drawdown, and time-aware metrics.
Accounts for dependence between nearby observations.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import (
    brier_score_loss,
    log_loss,
    roc_auc_score,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
)
import structlog
from typing import Optional
from dataclasses import dataclass, field

from app.ml.calibration import compute_calibration_metrics

logger = structlog.get_logger(__name__)


@dataclass
class EvaluationReport:
    """Comprehensive model evaluation results."""
    model_name: str
    direction: str
    n_samples: int

    # Probability metrics
    brier_score: float
    log_loss_val: float
    auc_roc: float
    calibration_slope: float
    calibration_intercept: float

    # Classification at threshold
    accuracy: float
    precision: float
    recall: float
    f1: float

    # Signal selection metrics
    selected_signal_count: int = 0
    selected_win_rate: float = 0.0
    selected_win_rate_ci_lower: float = 0.0
    selected_win_rate_ci_upper: float = 0.0

    # Financial metrics (where quotes exist)
    quote_based_net_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    max_losing_streak: int = 0

    # Baseline comparison
    baseline_win_rate: float = 0.0
    improvement_over_baseline: float = 0.0

    # Calibration plot data
    calibration_curve_data: dict = field(default_factory=dict)

    # Coverage
    signal_coverage: float = 0.0  # What fraction of time produces signals

    # Time block resampling
    block_bootstrap_ci: Optional[dict] = None


def evaluate_model(
    model_name: str,
    direction: str,
    y_true: np.ndarray,
    y_pred_proba: np.ndarray,
    threshold: float = 0.5,
    breakeven_probs: Optional[np.ndarray] = None,
    quote_prices: Optional[np.ndarray] = None,
    quote_payouts: Optional[np.ndarray] = None,
    times: Optional[pd.Series] = None,
    block_size: int = 100,
    n_bootstrap: int = 1000,
) -> EvaluationReport:
    """
    Comprehensive model evaluation.

    Args:
        model_name: Identifier for the model.
        direction: "upper" or "lower".
        y_true: True binary labels.
        y_pred_proba: Predicted probabilities.
        threshold: Classification threshold.
        breakeven_probs: Per-sample break-even probabilities (from quotes).
        quote_prices: Per-sample purchase prices.
        quote_payouts: Per-sample payouts.
        times: Sample timestamps for time-block resampling.
        block_size: Block size for time-block bootstrap.
        n_bootstrap: Number of bootstrap iterations.
    """
    y_pred = (y_pred_proba >= threshold).astype(int)

    # Core metrics
    brier = brier_score_loss(y_true, y_pred_proba)
    ll = log_loss(y_true, y_pred_proba, labels=[0, 1])

    try:
        auc = roc_auc_score(y_true, y_pred_proba)
    except ValueError:
        auc = 0.5

    acc = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1_val = f1_score(y_true, y_pred, zero_division=0)

    # Calibration
    cal_metrics = compute_calibration_metrics(y_true, y_pred_proba)

    # Signal selection (where model predicts above breakeven)
    selected_count = 0
    selected_win_rate = 0.0
    ci_lower, ci_upper = 0.0, 0.0
    net_return = None
    max_dd = None
    max_losing = 0

    if breakeven_probs is not None:
        selected_mask = y_pred_proba > breakeven_probs
        selected_count = int(selected_mask.sum())

        if selected_count > 0:
            selected_outcomes = y_true[selected_mask]
            selected_win_rate = float(selected_outcomes.mean())

            # Wilson confidence interval
            ci_lower, ci_upper = _wilson_ci(
                selected_outcomes.sum(), selected_count, confidence=0.95
            )

            # Financial metrics with quotes
            if quote_prices is not None and quote_payouts is not None:
                sel_prices = quote_prices[selected_mask]
                sel_payouts = quote_payouts[selected_mask]
                sel_outcomes = selected_outcomes

                pnl = np.where(
                    sel_outcomes == 1,
                    sel_payouts - sel_prices,  # Win: payout - stake
                    -sel_prices,               # Loss: lose stake
                )
                net_return = float(pnl.sum())

                # Maximum drawdown
                cumulative = np.cumsum(pnl)
                running_max = np.maximum.accumulate(cumulative)
                drawdowns = running_max - cumulative
                max_dd = float(drawdowns.max()) if len(drawdowns) > 0 else 0

                # Max losing streak
                max_losing = _max_losing_streak(sel_outcomes)

    # Baseline comparison
    baseline_rate = float(y_true.mean())

    # Time-block bootstrap for confidence intervals
    block_ci = None
    if times is not None and len(y_true) > block_size * 2:
        block_ci = _time_block_bootstrap(
            y_true, y_pred_proba, times, block_size, n_bootstrap
        )

    return EvaluationReport(
        model_name=model_name,
        direction=direction,
        n_samples=len(y_true),
        brier_score=brier,
        log_loss_val=ll,
        auc_roc=auc,
        calibration_slope=cal_metrics["calibration_slope"],
        calibration_intercept=cal_metrics["calibration_intercept"],
        accuracy=acc,
        precision=prec,
        recall=rec,
        f1=f1_val,
        selected_signal_count=selected_count,
        selected_win_rate=selected_win_rate,
        selected_win_rate_ci_lower=ci_lower,
        selected_win_rate_ci_upper=ci_upper,
        quote_based_net_return=net_return,
        max_drawdown=max_dd,
        max_losing_streak=max_losing,
        baseline_win_rate=baseline_rate,
        improvement_over_baseline=selected_win_rate - baseline_rate if selected_count > 0 else 0,
        calibration_curve_data=cal_metrics,
        signal_coverage=selected_count / len(y_true) if len(y_true) > 0 else 0,
        block_bootstrap_ci=block_ci,
    )


def _wilson_ci(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score confidence interval for a proportion."""
    from scipy import stats

    if n == 0:
        return 0.0, 0.0

    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p = successes / n

    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    spread = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom

    return max(0, center - spread), min(1, center + spread)


def _max_losing_streak(outcomes: np.ndarray) -> int:
    """Calculate maximum consecutive losses."""
    max_streak = 0
    current = 0
    for o in outcomes:
        if o == 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _time_block_bootstrap(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    times: pd.Series,
    block_size: int,
    n_bootstrap: int,
) -> dict:
    """
    Time-block bootstrap to account for dependence between nearby observations.
    Does NOT treat overlapping windows as independent.
    """
    n = len(y_true)
    n_blocks = n // block_size

    brier_scores = []
    for _ in range(n_bootstrap):
        # Sample blocks with replacement
        block_starts = np.random.randint(0, n - block_size, size=n_blocks)
        indices = np.concatenate([np.arange(s, s + block_size) for s in block_starts])
        indices = indices[indices < n]

        if len(indices) < 10:
            continue

        bs_brier = brier_score_loss(y_true[indices], y_pred[indices])
        brier_scores.append(bs_brier)

    if brier_scores:
        return {
            "brier_mean": float(np.mean(brier_scores)),
            "brier_std": float(np.std(brier_scores)),
            "brier_ci_lower": float(np.percentile(brier_scores, 2.5)),
            "brier_ci_upper": float(np.percentile(brier_scores, 97.5)),
            "n_bootstrap": n_bootstrap,
            "block_size": block_size,
        }
    return None
