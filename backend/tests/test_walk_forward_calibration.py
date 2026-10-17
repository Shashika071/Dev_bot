"""Fold comparisons use calibration labels fairly and preserve the final lockbox."""

import sys
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from app.ml.walk_forward_eval import (
    aggregate_walk_forward,
    evaluate_outer_folds,
    score_fold_briers,
)


class ConstantCandidate:
    probability = 0.5

    def fit(self, *args):
        pass

    def predict_proba(self, X):
        return np.full(len(X), self.probability)


@pytest.fixture
def fold_inputs(monkeypatch):
    from app.ml import baseline

    class HighConstantCandidate(ConstantCandidate):
        probability = 0.99

    monkeypatch.setattr(baseline, "LogisticRegressionBaseline", ConstantCandidate)
    monkeypatch.setattr(baseline, "VolatilityDistanceBaseline", HighConstantCandidate)
    return {
        "X_train": pd.DataFrame({"row": np.arange(40)}),
        "y_train": pd.Series([1] * 4 + [0] * 36),
        "X_val": pd.DataFrame({"row": np.arange(50, 62)}),
        "y_val": pd.Series([0, 1] * 6),
        "X_cal": pd.DataFrame({"row": np.arange(70, 170)}),
        "y_cal": pd.Series([1, 1, 1, 1, 0] * 20),
        "X_test": pd.DataFrame({"row": np.arange(200, 220)}),
        "y_test": pd.Series([1, 1, 1, 1, 0] * 4),
        "direction": "upper",
    }


def test_fold_compares_calibrated_candidates_to_recent_frequency(fold_inputs):
    scores = score_fold_briers(**fold_inputs, include_heavy_models=False)

    # Train frequency is 10%, but all candidates have access to an 80% cal rate.
    # Raw 50% and 99% constants must both become equally uninformative 80% scores.
    assert scores["baseline"] == pytest.approx(0.16)
    assert scores["logistic_regression"] == pytest.approx(0.16, abs=1e-7)
    assert scores["volatility_distance"] == pytest.approx(0.16, abs=1e-7)
    summary = aggregate_walk_forward(
        [{"n_test": 40, "briers": scores} for _ in range(5)]
    )
    assert summary["selected_pipeline"] is None


def test_test_labels_never_refit_fold_calibration(fold_inputs):
    fold_inputs["y_test"] = pd.Series(np.zeros(20))
    scores = score_fold_briers(**fold_inputs, include_heavy_models=False)

    # An accidental calibration on test would produce a zero rate and near-zero
    # error. These predictions retain the earlier calibration-period 80% rate.
    assert scores["baseline"] == pytest.approx(0.64)
    assert scores["logistic_regression"] == pytest.approx(0.64, abs=5e-4)
    assert scores["volatility_distance"] == pytest.approx(0.64, abs=5e-4)


@pytest.mark.parametrize("unavailable", ["missing", "short", "single_class", "nonbinary", "misaligned"])
def test_unusable_calibration_skips_the_fold(fold_inputs, unavailable):
    if unavailable == "missing":
        fold_inputs.pop("X_cal")
        fold_inputs.pop("y_cal")
    elif unavailable == "short":
        fold_inputs["X_cal"] = fold_inputs["X_cal"].iloc[:10]
        fold_inputs["y_cal"] = fold_inputs["y_cal"].iloc[:10]
    elif unavailable == "single_class":
        fold_inputs["y_cal"] = pd.Series(np.ones(100))
    elif unavailable == "nonbinary":
        fold_inputs["y_cal"] = pd.Series([0.2, 0.8] * 50)
    else:
        fold_inputs["y_cal"] = fold_inputs["y_cal"].iloc[:-1]

    assert score_fold_briers(**fold_inputs, include_heavy_models=False) == {}


def test_failed_model_predictions_do_not_replace_other_candidates(fold_inputs, monkeypatch):
    from app.ml import baseline

    class InvalidCandidate(ConstantCandidate):
        def predict_proba(self, X):
            return np.full(len(X), np.nan)

    monkeypatch.setattr(baseline, "LogisticRegressionBaseline", InvalidCandidate)
    scores = score_fold_briers(**fold_inputs, include_heavy_models=False)

    assert "logistic_regression" not in scores
    assert scores["baseline"] == pytest.approx(0.16)
    assert scores["volatility_distance"] == pytest.approx(0.16, abs=1e-7)


def test_heavy_models_stop_on_validation_and_calibrate_on_separate_slice(fold_inputs, monkeypatch):
    fitted = {}
    predicted = {}

    def install(module_name, class_name, model_name):
        class RecordingModel(ConstantCandidate):
            def __init__(self, **kwargs):
                pass

            def fit(self, *args):
                fitted[model_name] = args

            def predict_proba(self, X):
                predicted.setdefault(model_name, []).append(X)
                return super().predict_proba(X)

        module = ModuleType(module_name)
        setattr(module, class_name, RecordingModel)
        monkeypatch.setitem(sys.modules, module_name, module)

    install("app.ml.xgboost_model", "XGBoostTouchModel", "xgboost")
    install("app.ml.catboost_model", "CatBoostTouchModel", "catboost")
    install("app.ml.lstm_model", "LSTMTouchModel", "lstm")
    for segment in ("train", "val", "cal", "test"):
        fold_inputs[f"seq_{segment}"] = np.full(
            (len(fold_inputs[f"X_{segment}"]), 2, 4),
            {"train": 1, "val": 2, "cal": 3, "test": 4}[segment],
        )

    scores = score_fold_briers(**fold_inputs)

    for model_name in ("xgboost", "catboost"):
        X_train, y_train, X_es, y_es = fitted[model_name]
        assert X_train is fold_inputs["X_train"]
        assert y_train is fold_inputs["y_train"]
        assert X_es is fold_inputs["X_val"]
        assert y_es is fold_inputs["y_val"]
        assert predicted[model_name][0] is fold_inputs["X_cal"]
        assert predicted[model_name][1] is fold_inputs["X_test"]
        assert model_name in scores
    seq_train, y_train, seq_es, y_es = fitted["lstm"]
    assert seq_train is fold_inputs["seq_train"]
    assert seq_es is fold_inputs["seq_val"]
    np.testing.assert_array_equal(y_train, fold_inputs["y_train"])
    np.testing.assert_array_equal(y_es, fold_inputs["y_val"])
    assert predicted["lstm"][0] is fold_inputs["seq_cal"]
    assert predicted["lstm"][1] is fold_inputs["seq_test"]
    assert "lstm" in scores


def test_outer_folds_forward_distinct_slices_and_keep_lockbox_untouched(monkeypatch):
    from app.ml import walk_forward_eval

    n = 1000
    features = pd.DataFrame({"row": np.arange(n)})
    labels = pd.Series([0, 1] * (n // 2))
    times = pd.Series(pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"))
    sequences = np.broadcast_to(np.arange(n)[:, None, None], (n, 2, 4)).copy()
    observed = []
    monkeypatch.setattr(walk_forward_eval, "fit_feature_prep", lambda X: {})
    monkeypatch.setattr(walk_forward_eval, "apply_feature_prep", lambda X, prep: X)

    def inspect_fold(X_train, y_train, X_test, y_test, **kwargs):
        frames = [X_train, kwargs["X_val"], kwargs["X_cal"], X_test]
        seqs = [kwargs["seq_train"], kwargs["seq_val"], kwargs["seq_cal"], kwargs["seq_test"]]
        for earlier, later in zip(frames, frames[1:]):
            assert earlier["row"].max() < later["row"].min()
        for frame, seq in zip(frames, seqs):
            np.testing.assert_array_equal(frame["row"], seq[:, 0, 0])
        assert X_test["row"].max() < 800
        observed.append(len(X_test))
        return {"baseline": 0.2, "logistic_regression": 0.1}

    monkeypatch.setattr(walk_forward_eval, "score_fold_briers", inspect_fold)
    summary, lock_start = evaluate_outer_folds(
        features, labels, times, sequences,
        direction="upper", outcome_window_seconds=540, gap_seconds=600,
        lockbox_fraction=0.2,
    )

    assert lock_start == 800
    assert len(observed) == 5
    assert summary["folds_total"] == 5
    assert summary["folds_eligible"] == 5
    assert summary["metrics_kind"] == "calibrated"
    assert summary["baseline_kind"] == "calibration_frequency"


def test_equal_scores_are_not_a_baseline_win():
    rows = [{"n_test": 40, "briers": {"baseline": 0.16, "constant": 0.16}}] * 5
    summary = aggregate_walk_forward(rows)

    assert summary["models"]["constant"]["win_fraction"] == 0.0
    assert summary["selected_pipeline"] is None
