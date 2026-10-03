"""Ensemble weighted average and stacking maths."""

import numpy as np

from app.ml.ensemble import (
    equal_weights,
    fit_stacking_ensemble,
    fit_weighted_ensemble,
    blend_predictions,
)


def test_equal_weights_sum_to_one():
    w = equal_weights()
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(v >= 0 for v in w.values())


def test_weighted_ensemble_prefers_better_model():
    y = np.array([1, 0, 1, 0, 1, 0, 1, 0, 1, 0] * 5)
    good = y.astype(float) * 0.9 + 0.05
    bad = np.full(len(y), 0.5)
    worse = 1.0 - good
    ens = fit_weighted_ensemble(
        {"xgboost": good, "catboost": bad, "lstm": worse},
        y,
        grid_step=0.25,
    )
    assert ens.weights["xgboost"] >= ens.weights["lstm"]
    blended = ens.predict({"xgboost": good, "catboost": bad, "lstm": worse})
    assert blended.shape == y.shape


def test_stacking_uses_oof_style_inputs():
    rng = np.random.default_rng(0)
    y = (rng.random(80) > 0.4).astype(float)
    p1 = np.clip(y * 0.7 + rng.normal(0, 0.1, 80), 0.01, 0.99)
    p2 = np.clip(y * 0.6 + rng.normal(0, 0.15, 80), 0.01, 0.99)
    p3 = np.clip(rng.random(80), 0.01, 0.99)
    stack = fit_stacking_ensemble({"xgboost": p1, "catboost": p2, "lstm": p3}, y)
    out = stack.predict({"xgboost": p1[:10], "catboost": p2[:10], "lstm": p3[:10]})
    assert len(out) == 10
    assert np.all((out >= 0) & (out <= 1))


def test_blend_does_not_average_stacking_with_inputs():
    preds = {
        "xgboost": np.array([0.9]),
        "catboost": np.array([0.8]),
        "lstm": np.array([0.7]),
    }
    from app.ml.ensemble import WeightedEnsemble
    w = WeightedEnsemble(weights=equal_weights())
    out = blend_predictions("weighted", preds, weighted=w)
    # Pure weighted average, not mixed with a fourth stacking term
    assert abs(float(out[0]) - (0.9 + 0.8 + 0.7) / 3) < 1e-9
