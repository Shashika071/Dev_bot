"""The expanded inputs and chosen policy must survive offline/live boundaries."""
import json
import pickle

import numpy as np
import pytest

from app.accuracy.core import SCHEMA, features
from app.accuracy.technical import FEATURE_NAMES, HISTORY_SECONDS, TechnicalContext
from app.accuracy.technical_training import feature_groups, select_policy
from app.accuracy.training import Predictor, load_artifact


def _names():
    t = np.arange(0, 1202, 2)
    return list(features(t, np.full(len(t), 100.), 1200)) + list(FEATURE_NAMES)


def _trial(name, low, count=400, per_day=13, brier=.03):
    return {"model": name, "signals": count, "signals_per_full_day": per_day,
            "full_day_cluster_ci95": [low, .99], "brier": brier, "threshold": .97}


def test_all_nine_families_have_real_input_columns():
    groups = feature_groups(_names())
    assert len(groups) == 9
    assert all(groups.values())
    columns = [name for names in groups.values() for name in names]
    assert len(columns) == len(set(columns)) == len(FEATURE_NAMES)
    assert set(columns) == set(FEATURE_NAMES)


def test_selection_uses_development_bounds_and_preserves_coverage():
    rows = [_trial("accurate_but_rare", .99, per_day=3),
            _trial("too_few_outcomes", .99, count=20),
            _trial("high_test_rate", .92), _trial("stronger_development", .94)]
    rows[2]["test_touch_rate"] = 1.
    rows[3]["test_touch_rate"] = .9
    assert select_policy(rows)["model"] == "stronger_development"
    with pytest.raises(ValueError):
        select_policy(rows[:2])


def test_live_predictor_receives_the_same_expanded_columns_as_offline(monkeypatch):
    t = np.arange(0, HISTORY_SECONDS + 3602, 2)
    p = 100 + .1 * np.sin(t / 83) + .00001 * t
    epoch = HISTORY_SECONDS
    expected = features(t, p, epoch)
    expected.update(TechnicalContext(t, p).snapshot(epoch))
    model = Predictor("capture", _names(), feature_profile="technical_v2")
    captured = []

    def predict(matrix):
        captured.append(np.asarray(matrix))
        return np.array([[.98, .96]])

    monkeypatch.setattr(model, "predict", predict)
    assert model.history_seconds == HISTORY_SECONDS
    np.testing.assert_array_equal(model.snapshot(t[t <= epoch], p[t <= epoch], epoch), [.98, .96])
    np.testing.assert_allclose(captured[0], [[expected[name] for name in model.feature_names]])
    assert model.snapshot(t[-1000:], p[-1000:], int(t[-1])) is None


def _save(tmp_path, model, report, profile=None):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    (tmp_path / "latest.json").write_text(json.dumps({"run_id": "run"}), encoding="utf-8")
    bundle = {"schema": SCHEMA, "predictor": model, "report": report, "threshold": .97}
    if profile:
        bundle["feature_profile"] = profile
    (run / "bundle.pkl").write_bytes(pickle.dumps(bundle))


def test_existing_compact_pickle_remains_loadable_without_new_attribute(tmp_path):
    model = Predictor("frequency", ["a"], (.97, .96))
    del model.feature_profile
    _save(tmp_path, model, {})
    restored = load_artifact(tmp_path)["predictor"]
    assert restored.history_seconds == 900
    np.testing.assert_array_equal(restored.predict([[1]]), [[.97, .96]])


def test_matching_but_wrong_column_manifest_is_rejected(tmp_path):
    names = _names()
    report = {"feature_profile": "technical_v2", "feature_names": names,
              "feature_groups": feature_groups(names)}
    model = Predictor("frequency", names, (.97, .96), feature_profile="technical_v2")
    _save(tmp_path, model, report, "technical_v2")
    assert load_artifact(tmp_path)["predictor"].feature_names == names
    # A report and predictor agreeing with each other is not enough: the code
    # must implement that exact ordered schema too.
    broken = names.copy(); broken[-1] = "tech_future_nonexistent_feature"
    model.feature_names = broken; report["feature_names"] = broken
    _save(tmp_path, model, report, "technical_v2")
    with pytest.raises(ValueError, match="column order"):
        load_artifact(tmp_path)
