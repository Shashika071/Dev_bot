"""Feature column parity and per-sample leakage check."""

import pandas as pd

from app.features.pipeline import (
    ALL_FEATURES,
    get_feature_columns,
    select_feature_matrix,
    validate_no_future_leakage,
)


def test_select_feature_matrix_stable_order():
    df = pd.DataFrame({"return_10": [0.1], "noise": [9.0], "rsi_14": [50.0]})
    out = select_feature_matrix(df)
    assert list(out.columns) == get_feature_columns()
    assert list(out.columns) == ALL_FEATURES
    assert "noise" not in out.columns
    assert out["return_10"].iloc[0] == 0.1


def test_validate_no_future_leakage_ok():
    assert validate_no_future_leakage([100, 200], [100, 201]) is True


def test_validate_no_future_leakage_detects():
    assert validate_no_future_leakage([101], [100]) is False


def test_validate_length_mismatch():
    assert validate_no_future_leakage([1, 2], [1]) is False
