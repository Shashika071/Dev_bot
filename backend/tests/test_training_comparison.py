"""Exercise the training comparison, frozen selection, and report contract end to end."""

from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.dataset_builder import TouchDataset
from app.ml.trainer import TrainingOrchestrator


class ConstantEstimator:
    def __init__(self, **kwargs):
        pass

    def fit(self, *args, **kwargs):
        return self

    def predict_proba(self, frame):
        return np.full(len(frame), .5)

    def feature_importance(self):
        return pd.DataFrame({"feature": ["signal"], "importance": [1.]})

    def save(self, path):
        Path(path).write_bytes(b"isolated-test-estimator")


def test_test_labels_cannot_change_candidate_selection_or_calibration(monkeypatch, tmp_path):
    import app.ml.trainer as training

    n = 1000
    times = pd.Series(pd.to_datetime(1_700_000_000 + np.arange(n) * 1200, unit="s", utc=True))
    labels = (np.arange(n) % 5 != 0).astype(int)
    features = pd.DataFrame({
        "signal": np.zeros(n),
        "realized_vol_300": np.ones(n),
        "barrier_distance_vol_normalized": np.ones(n),
    })
    sequences = np.zeros((n, 64, 4), dtype=np.float32)
    monkeypatch.setattr(training, "XGBoostTouchModel", ConstantEstimator)
    monkeypatch.setattr(training, "CatBoostTouchModel", ConstantEstimator)
    monkeypatch.setattr(training, "LSTMTouchModel", ConstantEstimator)
    monkeypatch.setattr("app.ml.device.describe_train_devices", lambda: {"torch": "cpu", "catboost": "CPU", "xgboost": "cpu"})
    monkeypatch.setattr(TrainingOrchestrator, "_sample_entry_times", lambda *args: times)
    monkeypatch.setattr(training, "evaluate_outer_folds", lambda *args, **kwargs: ({
        "folds_eligible": 5,
        "selected_pipeline": "catboost",
        "models": {"catboost": {"n_folds": 5, "win_fraction": 1.}},
    }, 880))

    results = []
    maps = []
    for run, flip_test in enumerate((False, True)):
        y = labels.copy()
        if flip_test:
            y[880:] = 1 - y[880:]
        dataset = TouchDataset(
            features=features, sequences=sequences,
            labels=pd.Series(y), times=times,
            unresolved=0, resolved_before_alignment=n, leakage=False, aligned_rows=n,
        )
        monkeypatch.setattr(training, "build_touch_dataset", lambda *args, **kwargs: dataset)
        model_dir = tmp_path / str(run)
        monkeypatch.setattr(training.settings, "model_dir", str(model_dir))
        model = TrainingOrchestrator()
        result = model.train_full_pipeline(pd.DataFrame({"epoch": [1]}))
        assert "error" not in result
        results.append(result)
        maps.append(model.models["calibrator"].calibrate([.1, .9]))
        assert result["selected_pipeline"] == "baseline"
        assert not result["promotion_ready"]
        assert not result["has_demonstrated_edge"]
        assert result["validation_selection"]["temporal_order"] == ["train", "cal", "val", "test"]
        ranges = result["validation_selection"]["partition_ranges"]
        for earlier, later in (("train", "cal"), ("cal", "val"), ("val", "test")):
            assert pd.Timestamp(ranges[earlier]["end"]) < pd.Timestamp(ranges[later]["start"])
        for name, metrics in result["candidate_probability_metrics"].items():
            assert result[name]["brier_score"] == metrics["calibrated_brier"]
            assert metrics["n_samples"] == result["split_info"]["test"]
        if not flip_test:
            metrics = result["candidate_probability_metrics"]["catboost"]
            assert metrics["raw_brier"] > metrics["calibrated_brier"]
        assert Path(result["model_paths"]["report"]).is_file()
    assert results[0]["validation_selection"] == results[1]["validation_selection"]
    np.testing.assert_array_equal(maps[0], maps[1])
    assert results[0]["selected"]["brier_score"] != results[1]["selected"]["brier_score"]
