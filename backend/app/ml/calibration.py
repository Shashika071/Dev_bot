"""
Probability calibration — ensures model outputs are well-calibrated probabilities.
Uses separate calibration data (not training or validation data).
"""

import numpy as np
import pickle
import structlog
from typing import Optional

from sklearn.calibration import calibration_curve
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

logger = structlog.get_logger(__name__)


class ProbabilityCalibrator:
    """
    Calibrates raw model probabilities using isotonic or Platt (logistic)
    regression fitted on held-out calibration data.
    """

    def __init__(self, method: str = "auto", min_samples: int = 50):
        self.method = method
        self.min_samples = min_samples
        self._calibrator = None
        self._is_fitted = False
        self._raw_probs: Optional[np.ndarray] = None
        self._true_labels: Optional[np.ndarray] = None
        self._calibrated_probs: Optional[np.ndarray] = None
        self._chosen_method: str = method

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    def fit(self, raw_probabilities: np.ndarray, true_labels: np.ndarray):
        """
        Fit calibrator on held-out calibration data.
        MUST use separate data from training and validation.
        """
        raw = np.asarray(raw_probabilities, dtype=float).ravel()
        y = np.asarray(true_labels, dtype=float).ravel()
        if len(raw) < 11:
            raise ValueError(f"Need at least 11 calibration samples, got {len(raw)}")

        # Prefer Platt when cal set is small (isotonic overfits); isotonic when large.
        choose = self.method
        if choose == "auto":
            choose = "isotonic" if len(raw) >= self.min_samples else "platt"
        self._chosen_method = choose

        if choose == "isotonic":
            self._calibrator = IsotonicRegression(
                y_min=0.0, y_max=1.0, out_of_bounds="clip"
            )
            self._calibrator.fit(raw, y)
            calibrated = self._calibrator.predict(raw)
        elif choose == "platt":
            # Logistic regression on raw score as single feature
            lr = LogisticRegression(solver="lbfgs", max_iter=1000)
            lr.fit(raw.reshape(-1, 1), y.astype(int))
            self._calibrator = lr
            calibrated = lr.predict_proba(raw.reshape(-1, 1))[:, 1]
        else:
            raise ValueError(f"Unknown calibration method: {choose}")

        self._raw_probs = raw
        self._true_labels = y
        self._calibrated_probs = np.asarray(calibrated, dtype=float)
        self._is_fitted = True
        self.method = choose
        logger.info(
            "calibrator_fitted",
            method=choose,
            n_samples=len(raw),
        )

    def calibrate(self, raw_probabilities: np.ndarray) -> np.ndarray:
        """Transform raw probabilities to calibrated probabilities."""
        if not self._is_fitted or self._calibrator is None:
            raise RuntimeError("Calibrator not fitted")
        raw = np.asarray(raw_probabilities, dtype=float).ravel()
        if isinstance(self._calibrator, IsotonicRegression):
            return self._calibrator.predict(raw)
        return self._calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]

    def count_in_range(self, probability: float, band: float = 0.05) -> int:
        """Count held-out calibration samples near a probability."""
        if self._calibrated_probs is None:
            return 0
        lo = max(0.0, probability - band)
        hi = min(1.0, probability + band)
        return int(((self._calibrated_probs >= lo) & (self._calibrated_probs <= hi)).sum())

    def empirical_stats(self, probability: float, band: float = 0.05) -> dict:
        """
        Empirical hit rate and Wilson CI for held-out samples near `probability`.
        """
        if self._calibrated_probs is None or self._true_labels is None:
            return {
                "sample_count": 0,
                "hit_rate": 0.0,
                "ci_lower": 0.0,
                "ci_upper": 0.0,
            }

        lo = max(0.0, probability - band)
        hi = min(1.0, probability + band)
        mask = (self._calibrated_probs >= lo) & (self._calibrated_probs <= hi)
        n = int(mask.sum())
        if n == 0:
            return {
                "sample_count": 0,
                "hit_rate": 0.0,
                "ci_lower": 0.0,
                "ci_upper": 0.0,
            }

        successes = int(self._true_labels[mask].sum())
        hit_rate = successes / n
        ci_lower, ci_upper = _wilson_ci(successes, n)
        return {
            "sample_count": n,
            "hit_rate": float(hit_rate),
            "ci_lower": float(ci_lower),
            "ci_upper": float(ci_upper),
        }

    def save(self, path: str):
        if not self._is_fitted or self._calibrator is None:
            raise RuntimeError("Refusing to save unfitted calibrator")
        payload = {
            "method": self.method,
            "calibrator": self._calibrator,
            "raw_probs": self._raw_probs,
            "true_labels": self._true_labels,
            "calibrated_probs": self._calibrated_probs,
            "is_fitted": True,
        }
        with open(path, "wb") as f:
            pickle.dump(payload, f)

    def load(self, path: str):
        with open(path, "rb") as f:
            payload = pickle.load(f)

        # Backward compatible: old files stored bare IsotonicRegression
        if isinstance(payload, IsotonicRegression):
            self._calibrator = payload
            self._is_fitted = True
            self._raw_probs = None
            self._true_labels = None
            self._calibrated_probs = None
            logger.warning("calibrator_loaded_legacy_format", path=path)
            return

        if not isinstance(payload, dict) or not payload.get("is_fitted"):
            raise RuntimeError(f"Calibrator file is not fitted: {path}")

        self.method = payload.get("method", "isotonic")
        self._calibrator = payload["calibrator"]
        self._raw_probs = payload.get("raw_probs")
        self._true_labels = payload.get("true_labels")
        self._calibrated_probs = payload.get("calibrated_probs")
        self._is_fitted = True


def _wilson_ci(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    from scipy import stats

    if n == 0:
        return 0.0, 0.0
    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p = successes / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    spread = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, center - spread), min(1.0, center + spread)


def compute_calibration_metrics(
    true_labels: np.ndarray,
    predicted_probs: np.ndarray,
    n_bins: int = 10,
) -> dict:
    """Compute calibration metrics for evaluation."""
    prob_true, prob_pred = calibration_curve(
        true_labels, predicted_probs, n_bins=n_bins, strategy="uniform"
    )
    brier = brier_score_loss(true_labels, predicted_probs)
    ll = log_loss(true_labels, predicted_probs)

    if len(prob_true) >= 2:
        slope_fit = np.polyfit(prob_pred, prob_true, 1)
        cal_slope = float(slope_fit[0])
        cal_intercept = float(slope_fit[1])
    else:
        cal_slope = float("nan")
        cal_intercept = float("nan")

    return {
        "brier_score": float(brier),
        "log_loss": float(ll),
        "calibration_slope": cal_slope,
        "calibration_intercept": cal_intercept,
        "prob_true": prob_true.tolist(),
        "prob_pred": prob_pred.tolist(),
        "n_bins": n_bins,
    }
