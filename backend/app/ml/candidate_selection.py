"""Fair probability comparison without using final-test labels to select a model."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.ml.calibration import ProbabilityCalibrator


def _probabilities(values, *, length: int | None = None) -> np.ndarray:
    values = np.asarray(values, dtype=float).ravel()
    if length is not None and len(values) != length:
        raise ValueError("Candidate predictions and labels are not aligned")
    if not np.isfinite(values).all():
        raise ValueError("Candidate predictions must be finite")
    if ((values < 0) | (values > 1)).any():
        raise ValueError("Candidate probabilities must be between zero and one")
    return values


def fit_candidate_calibrators(
    predictions: dict[str, np.ndarray],
    y_cal,
    min_samples: int = 50,
) -> dict[str, ProbabilityCalibrator]:
    """Fit each candidate on the same dedicated calibration observations.

    min_samples configures the calibrator's method threshold; admission and
    promotion requirements belong to the caller. No selection/test labels are
    accepted here. The baseline is the exact calibration-period event rate.
    """
    labels = np.asarray(y_cal, dtype=float).ravel()
    if len(labels) < 11 or not np.isfinite(labels).all():
        raise ValueError("Need at least 11 finite calibration labels")
    if not np.isin(labels, [0, 1]).all() or len(np.unique(labels)) != 2:
        raise ValueError("Calibration requires both binary outcome classes")
    if "baseline" not in predictions:
        raise ValueError("A frequency baseline is required for candidate comparison")
    calibrators = {}
    for name, values in predictions.items():
        raw = _probabilities(values, length=len(labels))
        calibrator = ProbabilityCalibrator(
            method="frequency" if name == "baseline" else "auto",
            min_samples=min_samples,
        )
        calibrator.fit(raw, labels)
        calibrators[name] = calibrator
    return calibrators


def calibrated_candidate_predictions(
    predictions: dict[str, np.ndarray],
    calibrators: dict[str, ProbabilityCalibrator],
) -> dict[str, np.ndarray]:
    """Use each candidate's own frozen mapping; never another model's map."""
    out = {}
    for name, values in predictions.items():
        if name not in calibrators:
            raise ValueError(f"Candidate {name} has no independent calibrator")
        out[name] = calibrators[name].calibrate(_probabilities(values))
    return out


def candidate_probability_metrics(y_true, raw_predictions, calibrated_predictions, calibrators):
    """Return raw and calibrated diagnostics on the caller's evaluation slice."""
    labels = np.asarray(y_true, dtype=float).ravel()
    result = {}
    for name, values in raw_predictions.items():
        raw = _probabilities(values, length=len(labels))
        calibrated = _probabilities(calibrated_predictions[name], length=len(labels))

        def auc(probabilities):
            return float(roc_auc_score(labels, probabilities)) if len(np.unique(labels)) == 2 else None

        result[name] = {
            "raw_brier": float(brier_score_loss(labels, raw)),
            "calibrated_brier": float(brier_score_loss(labels, calibrated)),
            "raw_auc": auc(raw),
            "calibrated_auc": auc(calibrated),
            "calibration_method": calibrators[name].method,
            "n_samples": len(labels),
        }
    return result


def select_calibrated_candidate(
    scores: dict[str, float],
    walk_forward: dict,
    *,
    min_outer_folds: int = 4,
    min_win_fraction: float = 0.8,
    epsilon: float = 1e-9,
) -> tuple[str, str]:
    """Require both chronological selection and outer-fold baseline wins.

    The final lockbox is deliberately not an input. A prior fold winner cannot
    override a failure against the more recent frequency baseline.
    """
    baseline = float(scores["baseline"])
    if not np.isfinite(baseline):
        raise ValueError("Frequency baseline score must be finite")
    eligible = []
    fold_models = walk_forward.get("models") or {}
    for name, score in scores.items():
        if name == "baseline" or not np.isfinite(score) or float(score) >= baseline - epsilon:
            continue
        folds = fold_models.get(name) or {}
        if int(folds.get("n_folds") or 0) < min_outer_folds:
            continue
        if float(folds.get("win_fraction") or 0) < min_win_fraction:
            continue
        eligible.append(name)
    if not eligible:
        return "baseline", (
            "Kept the calibration-period frequency baseline: no candidate beat it "
            "on the later selection period and enough calibrated outer folds."
        )
    chosen = min(eligible, key=lambda name: (float(scores[name]), name))
    return chosen, (
        f"Selected {chosen} using calibrated selection Brier and consistent outer-fold "
        "improvement over the calibration-period frequency baseline; final test was not used for selection."
    )
