"""Probability comparisons use separate maps and the same recent frequency information."""

import numpy as np
import pandas as pd
import pytest

from app.features.dataset import chronological_split
from app.ml.bundle import ModelPipelineBundle
from app.ml.calibration import ProbabilityCalibrator
from app.ml.candidate_selection import (
    calibrated_candidate_predictions,
    candidate_probability_metrics,
    fit_candidate_calibrators,
    select_calibrated_candidate,
)


def _folds(**wins):
    return {
        "folds_eligible": 5,
        "selected_pipeline": next(iter(wins), None),
        "models": {name: {"n_folds": 5, "win_fraction": fraction} for name, fraction in wins.items()},
    }


def test_baseline_uses_calibration_frequency_and_survives_save_load(tmp_path):
    y_cal = np.r_[np.ones(90), np.zeros(10)]
    calibrators = fit_candidate_calibrators({"baseline": np.full(100, .5)}, y_cal)
    baseline = calibrators["baseline"]
    np.testing.assert_allclose(baseline.calibrate([.01, .5, .99]), [.9, .9, .9])
    path = tmp_path / "frequency.pkl"
    baseline.save(str(path))
    restored = ProbabilityCalibrator()
    restored.load(str(path))
    assert restored.method == "frequency"
    np.testing.assert_allclose(restored.calibrate([.1, .9]), [.9, .9])
    bundle = ModelPipelineBundle({"selected_pipeline": "baseline", "baseline_touch_rate": .5})
    bundle.calibrator = restored
    prediction = bundle.predict_calibrated(pd.DataFrame({"unused": [1.]}))
    assert prediction["raw_probability"] == .5
    assert prediction["calibrated_probability"] == pytest.approx(.9)
    assert prediction["empirical_stats"]["sample_count"] == 100


def test_every_candidate_gets_its_own_calibrator_and_diagnostics():
    labels = np.tile([1., 1., 1., 0.], 500)
    raw = {
        "baseline": np.full(len(labels), .5),
        "tree": np.where(labels == 1, .6, .4),
        "uninformative": np.full(len(labels), .6),
    }
    calibrators = fit_candidate_calibrators(raw, labels)
    predictions = calibrated_candidate_predictions(raw, calibrators)
    np.testing.assert_allclose(predictions["baseline"], .75)
    np.testing.assert_allclose(predictions["uninformative"], .75)
    np.testing.assert_allclose(predictions["tree"], labels)
    metrics = candidate_probability_metrics(labels, raw, predictions, calibrators)
    assert metrics["tree"]["calibrated_brier"] < metrics["tree"]["raw_brier"]
    assert metrics["tree"]["calibrated_brier"] == 0
    assert metrics["baseline"]["calibration_method"] == "frequency"
    assert metrics["uninformative"]["calibrated_brier"] == pytest.approx(.1875)


def test_old_fold_winner_cannot_override_later_failure_or_a_tie():
    chosen, _ = select_calibrated_candidate(
        {"baseline": .08, "catboost": .12}, _folds(catboost=1.),
    )
    assert chosen == "baseline"
    chosen, _ = select_calibrated_candidate(
        {"baseline": .08, "catboost": .08}, _folds(catboost=1.),
    )
    assert chosen == "baseline"


def test_candidate_must_beat_recent_baseline_and_consistent_outer_folds():
    scores = {"baseline": .08, "catboost": .06, "xgboost": .05}
    chosen, _ = select_calibrated_candidate(scores, _folds(catboost=1., xgboost=.4))
    assert chosen == "catboost"
    assert select_calibrated_candidate(scores, {"models": {}})[0] == "baseline"
    folds = _folds(catboost=1.)
    folds["models"]["catboost"]["n_folds"] = 1
    assert select_calibrated_candidate(scores, folds)[0] == "baseline"


@pytest.mark.parametrize("bad", [np.full(20, np.nan), np.full(20, 2.), np.full(10, .5)])
def test_invalid_candidate_cannot_be_silently_compared(bad):
    with pytest.raises(ValueError):
        fit_candidate_calibrators(
            {"baseline": np.full(20, .5), "broken": bad}, np.tile([0., 1.], 10),
        )


def test_uncalibrated_candidate_cannot_borrow_another_mapping():
    calibrators = fit_candidate_calibrators({"baseline": np.full(20, .5)}, np.tile([0., 1.], 10))
    with pytest.raises(ValueError, match="no independent calibrator"):
        calibrated_candidate_predictions({"tree": np.array([.5])}, calibrators)


def test_earlier_calibration_period_has_minimum_samples_and_purged_boundaries():
    n = 400
    times = pd.Series(pd.to_datetime(1_700_000_000 + np.arange(n) * 540, unit="s", utc=True))
    split = chronological_split(
        pd.DataFrame({"x": np.arange(n)}), pd.Series(np.arange(n) % 2), times,
        min_val_samples=70, min_cal_samples=20,
    )
    assert len(split.X_val) >= 70
    # The trainer names these periods CAL then VAL, in this temporal order.
    for earlier, later in ((split.train_times, split.val_times), (split.val_times, split.cal_times), (split.cal_times, split.test_times)):
        assert (later.min() - earlier.max()).total_seconds() >= 1140
