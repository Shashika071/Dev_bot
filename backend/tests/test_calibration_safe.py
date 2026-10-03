"""Calibrator fails closed when evidence is insufficient; never saves unfitted."""

import os
import tempfile

import numpy as np
import pytest

from app.ml.calibration import ProbabilityCalibrator


def test_fit_rejects_insufficient_samples():
    cal = ProbabilityCalibrator()
    with pytest.raises(ValueError, match="at least 11"):
        cal.fit(np.linspace(0.1, 0.9, 5), np.array([0, 1, 0, 1, 0]))
    assert cal.is_fitted is False


def test_calibrate_rejects_unfitted():
    cal = ProbabilityCalibrator()
    with pytest.raises(RuntimeError, match="not fitted"):
        cal.calibrate(np.array([0.5]))


def test_save_rejects_unfitted():
    cal = ProbabilityCalibrator()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "cal.pkl")
        with pytest.raises(RuntimeError, match="unfitted"):
            cal.save(path)
        assert not os.path.exists(path)


def test_fit_save_load_roundtrip():
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.1, 0.9, 40)
    y = (raw > 0.5).astype(float)
    cal = ProbabilityCalibrator()
    cal.fit(raw, y)
    assert cal.is_fitted

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "cal.pkl")
        cal.save(path)
        loaded = ProbabilityCalibrator()
        loaded.load(path)
        assert loaded.is_fitted
        out = loaded.calibrate(np.array([0.6]))
        assert 0.0 <= float(out[0]) <= 1.0
        stats = loaded.empirical_stats(0.6, band=0.2)
        assert stats["sample_count"] >= 0
