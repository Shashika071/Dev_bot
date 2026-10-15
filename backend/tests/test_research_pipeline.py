"""Research pipeline: labels, candles, purge, leakage, and a small synthetic run."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.research_pipeline.candles import candle_tables, completed_asof
from app.research_pipeline.evaluate import (
    breakeven_probability,
    equal_weight_beats_best,
    expected_return_per_stake,
    select_threshold,
)
from app.research_pipeline.features import (
    build_tables,
    columns_for,
    future_mutation_changes_features,
    lookup_features,
)
from app.research_pipeline.io_ticks import matched_net_profit_r
from app.research_pipeline.labels import label_entries
from app.research_pipeline.quality import prepare_ticks
from app.research_pipeline.settings import load_config
from app.research_pipeline.splits import dependence_block_length, final_split


def _cfg(**overrides):
    base = dict(
        fast=True,
        target="upper_touch",
        horizon_seconds=60,
        barrier_distance=0.15,
        sample_every_seconds=60,
        entry_delay_seconds=0,
        max_gap_seconds=5,
        warmup_seconds=1500,
        symbol="R_100",
    )
    base.update(overrides)
    return load_config(**base)


def test_quality_reports_types_duplicates_and_timestamp_units():
    raw = pd.DataFrame(
        {
            "symbol": ["R_100", "R_100", "R_100", "R_100", "1HZ100V"],
            "epoch": [1_700_000_000_000, 1_700_000_002_000, 1_700_000_002_000, 1_700_000_004_000, 1_700_000_006_000],
            "price": [100.0, 100.0, 101.0, 100.0, 50.0],
        }
    )
    clean, report = prepare_ticks(raw, "R_100")
    assert list(report["raw_columns"]) == ["symbol", "epoch", "price"]
    assert report["timestamp_input"] == "milliseconds"
    assert report["timezone"] == "UTC"
    assert report["true_duplicate_rows_removed"] == 0
    assert report["conflicting_same_epoch_rows_removed"] == 1
    assert report["instrument"] == "R_100"
    assert clean["quote"].tolist() == [100.0, 100.0, 100.0]
    assert report["instruments"]["1HZ100V"] == 1

    repeated = pd.DataFrame(
        {
            "symbol": ["R_100", "R_100", "R_100"],
            "epoch": [10, 12, 12],
            "quote": [5.0, 5.0, 5.0],
        }
    )
    clean, report = prepare_ticks(repeated, "R_100")
    assert report["true_duplicate_rows_removed"] == 1
    assert clean["quote"].tolist() == [5.0, 5.0]

    invalid = pd.DataFrame({"symbol": ["R_100", "R_100"], "epoch": [1, 2], "quote": [10.0, -1.0]})
    clean, report = prepare_ticks(invalid, "R_100")
    assert report["invalid_price_rows_removed"] == 1
    assert len(clean) == 1


def test_completed_15s_candle_ignores_the_next_bucket():
    epochs = np.array([0, 10, 14, 15, 29, 30])
    quotes = np.array([10.0, 12.0, 11.0, 20.0, 18.0, 40.0])
    _all, usable = candle_tables(epochs, quotes, 15, max_gap_seconds=30)
    at_15 = completed_asof(usable, 15)
    assert at_15["bucket"].tolist() == [0]
    assert at_15.iloc[0][["open", "high", "low", "close"]].tolist() == [10.0, 12.0, 10.0, 11.0]
    at_30 = completed_asof(usable, 30)
    second = at_30.loc[at_30["bucket"] == 15].iloc[0]
    assert second[["open", "high", "low", "close"]].tolist() == [20.0, 20.0, 18.0, 18.0]
    changed = quotes.copy()
    changed[-1] = 99.0
    _all2, usable2 = candle_tables(epochs, changed, 15, max_gap_seconds=30)
    again = completed_asof(usable2, 30)
    assert again.loc[again["bucket"] == 15, "close"].iloc[0] == 18.0


def test_touch_and_direction_labels():
    cfg = _cfg(entry_delay_seconds=0, horizon_seconds=50, barrier_distance=1.0, max_gap_seconds=12)
    epochs = np.array([1000, 1010, 1020, 1030, 1040, 1050])
    quotes = np.array([100.0, 101.5, 98.5, 100.0, 100.1, 100.2])
    labels = label_entries(epochs, quotes, [1000], cfg)
    row = labels.iloc[0]
    assert row["status"] == "ok"
    assert row["y_upper"] == 1.0
    assert row["y_lower"] == 1.0
    assert row["y_direction"] == 1.0

    gap = label_entries(np.array([1000, 1010, 1050]), np.array([100.0, 102.0, 100.0]), [1000], cfg)
    assert gap.iloc[0]["status"] == "material_gap"

    short = label_entries(epochs[:-1], quotes[:-1], [1000], cfg)
    assert short.iloc[0]["status"] == "incomplete_future"

    delayed = _cfg(entry_delay_seconds=15, horizon_seconds=50, barrier_distance=1.0, max_gap_seconds=5)
    dense_epoch = np.arange(1000, 1051, 2)
    dense_price = np.full(len(dense_epoch), 100.2)
    dense_price[0] = 100.0
    dense_price[5] = 102.0  # epoch 1010, before the delay window
    delayed_labels = label_entries(dense_epoch, dense_price, [1000], delayed)
    assert delayed_labels.iloc[0]["status"] == "ok"
    assert delayed_labels.iloc[0]["y_upper"] == 0.0

    tie_epochs = np.array([1000, 1010, 1050])
    tie_quotes = np.array([100.0, 100.0, 100.0])
    tie_cfg = _cfg(horizon_seconds=50, barrier_distance=1.0, max_gap_seconds=60, tie_policy="exclude")
    tie = label_entries(tie_epochs, tie_quotes, [1000], tie_cfg)
    assert bool(tie.iloc[0]["tie"]) is True
    assert pd.isna(tie.iloc[0]["y_direction"])


def test_research_labels_match_live_touch_labeler_on_a_dense_path():
    from app.features.labels import label_touch_outcomes

    epochs = np.arange(1_000, 1_080, 2)
    quotes = np.full(len(epochs), 100.0)
    quotes[10] = 101.2
    ticks = pd.DataFrame({"epoch": epochs, "quote": quotes, "tick_time": pd.to_datetime(epochs, unit="s", utc=True)})
    live = label_touch_outcomes(
        ticks,
        pd.Series([1000]),
        duration_seconds=60,
        barrier_distance=1.0,
        barrier_direction="upper",
        entry_delay_seconds=5,
    )
    cfg = _cfg(horizon_seconds=60, barrier_distance=1.0, entry_delay_seconds=5, max_gap_seconds=5)
    research = label_entries(epochs, quotes, [1000], cfg)
    assert bool(live.iloc[0]["touched"]) is True
    assert research.iloc[0]["y_upper"] == 1.0
    assert research.iloc[0]["status"] == "ok"


def test_future_tick_and_unfinished_candle_do_not_change_features():
    cfg = _cfg(max_gap_seconds=5)
    n = 4000
    epochs = np.arange(1_700_000_000, 1_700_000_000 + n)
    quotes = 100 + np.sin(np.arange(n) / 20.0) * 0.2
    asof = int(epochs[0] + 2400)
    assert future_mutation_changes_features(epochs, quotes, asof, cfg) is False

    tables = build_tables(epochs, quotes, cfg)
    original = lookup_features(tables, asof, cfg)
    assert original is not None
    assert original["feature_close_time"] <= asof
    spike = quotes.copy()
    spike[epochs > original["feature_close_time"]] = 500.0
    # A spike inside the still-open candle must not change the completed-candle features.
    open_spike_epoch = original["feature_close_time"] + 10
    spike[epochs == open_spike_epoch] = 500.0
    changed = lookup_features(build_tables(epochs, spike, cfg), asof, cfg)
    assert changed["range_norm"] == original["range_norm"]
    assert changed["feature_close_time"] == original["feature_close_time"]


def test_purge_depends_on_horizon_not_a_fixed_row_count():
    epochs = np.array([0, 600, 750, 900, 1000], dtype=float)
    _dev, test, purged, test_start = final_split(epochs, horizon_seconds=100, test_fraction=0.2)
    assert test_start == 800
    assert test.tolist() == [False, False, False, True, True]
    # 750 is before the test, but 750 + 100 reaches past 800, so that row is purged.
    assert purged.tolist() == [False, False, True, False, False]
    _dev2, _test2, purged_short, _start = final_split(epochs, horizon_seconds=10, test_fraction=0.2)
    assert purged_short.tolist() == [False, False, False, False, False]
    assert dependence_block_length(540, 60) == 9
    assert dependence_block_length(540, 540) == 1


def test_payout_math_and_threshold_are_not_a_fixed_70_percent():
    assert abs(expected_return_per_stake(0.6, 1.0) - 0.2) < 1e-12
    assert breakeven_probability(1.0) == 0.5
    none = select_threshold(np.array([1, 0, 1]), np.array([0.9, 0.2, 0.8]), None, 1, 20, 1)
    assert none["threshold"] is None
    y = np.array([1, 1, 1, 1, 1, 0, 0, 0], dtype=float)
    p = np.array([0.95, 0.9, 0.85, 0.8, 0.75, 0.2, 0.1, 0.05])
    chosen = select_threshold(y, p, net_profit_r=1.0, block=1, n_boot=80, seed=1)
    assert chosen["threshold"] is not None
    assert chosen["threshold"] >= 0.5
    assert chosen["reason"].startswith("selected on chronological validation")

    bad = np.array([0.2, 0.8, 0.2, 0.8, 0.2, 0.8])
    good = np.array([0.8, 0.2, 0.8, 0.2, 0.8, 0.2])
    labels = np.array([1, 0, 1, 0, 1, 0], dtype=float)
    decision = equal_weight_beats_best(labels, {"bad": bad, "good": good})
    assert decision["kept"] is False
    assert decision["best_single"] == "good"


def test_quote_r_uses_development_decisions_only():
    quotes = pd.DataFrame(
        {
            "epoch": [100, 200, 10_000],
            "ask_price": [1.0, 1.0, 1.0],
            "payout": [1.5, 1.5, 100.0],
            "barrier_direction": ["upper", "upper", "upper"],
            "duration_seconds": [60, 60, 60],
            "barrier_distance": [0.15, 0.15, 0.15],
        }
    )
    # Five development matches are required, so repeat the cheap quote.
    quotes = pd.concat([quotes.iloc[[0, 1]]] * 3 + [quotes.iloc[[2]]], ignore_index=True)
    quotes.loc[:5, "epoch"] = [100, 120, 140, 160, 180, 200]
    result = matched_net_profit_r(
        quotes,
        [150, 160, 170, 190, 210],
        target="upper_touch",
        horizon_seconds=60,
        barrier_distance=0.15,
    )
    assert result["source"] == "historical_quotes"
    assert abs(result["r"] - 0.5) < 1e-9


def test_train_command_accepts_symbol_once(monkeypatch):
    from app.research_pipeline.__main__ import main
    from app.research_pipeline.io_ticks import psycopg_dsn

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["inspect", "--symbol", "R_100"]) == 2
    assert main(["train", "--symbol", "R_100", "--out", "unused"]) == 2
    assert psycopg_dsn("postgresql+asyncpg://user:secret@postgres:5432/deriv_bot") == (
        "postgresql://user:secret@postgres:5432/deriv_bot"
    )


def test_feature_groups_add_indicators_without_using_tick_count_as_volume():
    assert "tick_count" not in columns_for("strategy", "upper_touch")
    assert "ret_1m" in columns_for("returns_vol", "direction")
    assert "rsi_14" not in columns_for("returns_vol", "direction")
    assert "rsi_14" in columns_for("indicators", "direction")
    assert "trend_up" in columns_for("strategy", "direction")
    assert "barrier_over_vol" in columns_for("returns_vol", "upper_touch")
    assert "barrier_over_vol" not in columns_for("strategy", "direction")


def test_pipeline_on_synthetic_ticks_opens_test_once_and_logs_a_demo(tmp_path):
    from app.research_pipeline.run import run_experiment

    rng = np.random.default_rng(0)
    n = 9000
    epochs = np.arange(1_700_000_000, 1_700_000_000 + n)
    price = 100 + np.sin(np.arange(n) / 30.0) * 0.5 + rng.normal(0, 0.01, n)
    ticks = pd.DataFrame({"symbol": "R_100", "epoch": epochs, "quote": price})
    cfg = _cfg(target="upper_touch", enable_lstm=True, hypothetical_r=None)
    report = run_experiment(ticks, cfg, tmp_path)
    assert report["experimental"] is True
    assert report["data_quality"]["future_tick_changes_earlier_features"] is False
    assert report["data_quality"]["tick_count"] == n
    body = report["targets"]["upper_touch"]
    assert body["test_opened"] is True
    assert body["labeled_examples"] > body["rows"]["test"]
    assert body["test_metrics"]["logloss"] > 0
    assert body["test_metrics"]["roc_auc"] is None or 0 <= body["test_metrics"]["roc_auc"] <= 1
    assert body["selection"]["threshold"]["threshold"] is None
    assert body["demo_signal"]["order_placed"] is False
    assert body["demo_signal"]["decision"] == "no_signal"
    assert (tmp_path / "upper_touch" / "model.joblib").is_file()
    assert (tmp_path / "upper_touch" / "calibration.svg").is_file()
    assert (tmp_path / "report.md").is_file()
    assert "EXPERIMENTAL" in report["banner"]
