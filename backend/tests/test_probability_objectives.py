"""Uninformative features must preserve the event frequency before calibration."""

import numpy as np
import pandas as pd
import pytest

from app.ml.baseline import LogisticRegressionBaseline
from app.ml.catboost_model import CatBoostTouchModel
from app.ml.ensemble import fit_stacking_ensemble
from app.ml.xgboost_model import XGBoostTouchModel


def _uninformative_data(event_rate: float):
    # Every feature value has exactly the same outcome frequency, so a model
    # cannot distinguish classes by exploiting accidental finite-sample noise.
    groups = 30
    repeats = 20
    values = np.repeat(np.linspace(-1.0, 1.0, groups), repeats)
    positive = int(repeats * event_rate)
    labels = np.tile(np.r_[np.ones(positive), np.zeros(repeats - positive)], groups)
    order = np.random.default_rng(42).permutation(len(labels))
    return pd.DataFrame({"feature": values[order]}), pd.Series(labels[order].astype(int))


@pytest.mark.parametrize("event_rate", [0.1, 0.9])
@pytest.mark.parametrize("model_name", ["logistic", "catboost", "xgboost", "stacking"])
def test_probability_models_preserve_imbalanced_event_frequency(
    model_name, event_rate, monkeypatch, tmp_path
):
    # Device discovery must not touch CUDA, spawn nvidia-smi, or depend on the
    # machine used by CI. Any incidental model outputs stay in a temp directory.
    monkeypatch.setattr("app.ml.device.catboost_task_type", lambda: "CPU")
    monkeypatch.setattr("app.ml.device.xgboost_device_kwargs", lambda: {})
    monkeypatch.chdir(tmp_path)
    x, y = _uninformative_data(event_rate)
    if model_name == "logistic":
        model = LogisticRegressionBaseline()
    elif model_name == "catboost":
        model = CatBoostTouchModel(iterations=40, learning_rate=0.2, depth=2)
    elif model_name == "xgboost":
        model = XGBoostTouchModel(n_estimators=40, learning_rate=0.2, max_depth=2)
    else:
        # All three OOF score vectors vary, but none contains information about
        # outcomes. The meta-learner should retain the imbalanced event rate.
        predictions = {
            "xgboost": 0.5 + 0.2 * x["feature"].to_numpy(),
            "catboost": 0.5 - 0.3 * x["feature"].to_numpy(),
            "lstm": 0.5 + 0.1 * x["feature"].to_numpy(),
        }
        model = fit_stacking_ensemble(predictions, y.to_numpy())
        probability = model.predict(predictions)

    if model_name != "stacking":
        model.fit(x, y)
        probability = model.predict_proba(x)

    assert np.isfinite(probability).all()
    assert float(probability.mean()) == pytest.approx(event_rate, abs=0.025)
    assert float(np.std(probability)) < 0.04
    # At 90:10 (or 10:90), the useful frequency forecast scores about .09;
    # an artificially balanced .5 forecast scores .25.
    assert float(np.mean((probability - y.to_numpy()) ** 2)) < 0.12
    assert not (tmp_path / "catboost_info").exists()
