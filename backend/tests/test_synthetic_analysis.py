import copy
import json
import math

import pytest

from app.synthetic.analysis import (
    FEATURE_COLUMNS, INTERVALS, MIN_BARS, analyze_synthetic, chart_overlays,
    feature_table, features_from_frames,
)
from app.synthetic.indicators import frama_channel, range_filter


NOW = 1_792_396_800


def history(step, count=260, direction=1, breakout=False):
    end = NOW // step * step
    rows = []
    for index in range(count):
        close = 1000 + direction * (index * (0.002 if breakout else 0.15) + 0.025 * math.sin(index * 0.7))
        open_ = close - direction * 0.08
        rows.append({
            "epoch": end - (count - index) * step, "open": open_, "close": close,
            "high": max(open_, close) + 0.02, "low": min(open_, close) - 0.02,
        })
    if breakout:
        previous = rows[-2]["close"]
        close = previous + direction * 3
        rows[-1].update(open=previous, close=close, high=max(previous, close) + 0.02, low=min(previous, close) - 0.02)
    return rows


def frames(direction=1):
    return {name: history(step, direction=direction, breakout=name == "5m") for name, step in INTERVALS.items()}


@pytest.mark.parametrize("direction,expected", [(1, "RISE"), (-1, "FALL")])
def test_both_indicators_and_higher_timeframes_confirm_mirrored_entries(direction, expected):
    result = analyze_synthetic(frames(direction), now=NOW)
    assert result["eligible"], result["blockers"]
    assert result["direction"] == expected
    assert result["symbol"] == "R_100"
    assert result["expiry_seconds"] == 300
    assert result["decision_epoch"] == NOW
    assert result["indicators"]["frama"]["direction"] == expected
    assert result["indicators"]["range_filter"]["direction"] == expected
    assert result["features"]["checklist_direction"] == direction
    assert result["features"]["rule_eligible"] == 1
    json.dumps(result, allow_nan=False)


def test_rule_disagreement_or_opposite_higher_timeframe_blocks_entry():
    data = frames()
    data["1h"] = history(3600, direction=-1)
    result = analyze_synthetic(data, NOW)
    assert result["direction"] == "NO TRADE"
    assert not result["eligible"]
    assert result["features"]["rule_eligible"] == 0
    assert any("1h trend does not confirm RISE" in item for item in result["blockers"])


def test_in_channel_price_and_flat_filter_cannot_produce_entry():
    data = frames()
    for row in data["5m"]:
        row.update(open=1000, high=1000.1, low=999.9, close=1000)
    result = analyze_synthetic(data, NOW)
    assert result["direction"] == "NO TRADE"
    assert not result["eligible"]
    assert result["features"]["checklist_direction"] == 0
    assert result["features"]["rsi14"] == 50
    assert result["features"]["rule_eligible"] == 0


def test_stale_direction_does_not_repeatedly_create_entry_signal():
    data = frames()
    data["5m"] = history(300)
    result = analyze_synthetic(data, NOW)
    assert not result["eligible"]
    assert any("No fresh" in blocker for blocker in result["blockers"])


def test_future_and_forming_candles_cannot_change_features_or_analysis():
    data = frames()
    baseline = analyze_synthetic(data, NOW + 17)
    feature_baseline = features_from_frames(data, NOW + 17)
    for name, step in INTERVALS.items():
        for offset in (0, step, 10 * step):
            data[name].append({"epoch": NOW + offset, "open": 9, "high": 1e6, "low": 0.01, "close": 1e5})
    assert analyze_synthetic(data, NOW + 17) == baseline
    assert features_from_frames(data, NOW + 17) == feature_baseline


def test_indicator_outputs_are_prefix_invariant_including_signal_timestamps():
    data = history(300, count=320, breakout=True)
    for function in (frama_channel, range_filter):
        full = function(data)
        for length in (200, 220, 250, 300):
            assert function(data[:length]) == full[:length]


def test_batched_features_equal_live_for_every_available_prefix():
    data = frames()
    table = feature_table(data, NOW)
    assert len(table) >= 20
    assert table.columns.tolist() == ["decision_epoch", *FEATURE_COLUMNS]
    for index in (0, 5, len(table) - 1):
        point = table.iloc[index]
        epoch = int(point["decision_epoch"])
        live = features_from_frames(data, now=epoch)
        assert live is not None
        for name in FEATURE_COLUMNS:
            assert live[name] == pytest.approx(point[name], abs=1e-12), name
    baseline = table.iloc[:-1].copy()
    modified = copy.deepcopy(data)
    modified["5m"][-1].update(open=1000, high=1200, low=800, close=900)
    changed = feature_table(modified, NOW)
    assert changed.iloc[:-1].equals(baseline)


@pytest.mark.parametrize("name", list(INTERVALS))
def test_every_required_frame_requires_full_warmup(name):
    data = frames()
    data[name] = data[name][-(MIN_BARS - 1):]
    result = analyze_synthetic(data, NOW)
    assert not result["eligible"]
    assert features_from_frames(data, NOW) is None
    assert any(f"{name}: need {MIN_BARS}" in message for message in result["blockers"])


@pytest.mark.parametrize("mutation", [
    {"high": 0}, {"low": float("nan")}, {"close": float("inf")},
    {"open": -1}, {"epoch": NOW - 299}, {"epoch": NOW - 300.5},
])
def test_malformed_closed_candles_fail_closed(mutation):
    data = frames()
    data["5m"][-1].update(mutation)
    result = analyze_synthetic(data, NOW + 5)
    assert not result["eligible"]
    assert features_from_frames(data, NOW + 5) is None
    assert result["features"] == {}
    assert any("malformed" in message for message in result["blockers"])
    assert feature_table(data, NOW + 5).empty


def test_conflicting_duplicates_fail_closed_but_identical_duplicates_are_harmless():
    data = frames()
    baseline = analyze_synthetic(data, NOW)
    data["5m"].append(dict(data["5m"][-1]))
    assert analyze_synthetic(data, NOW) == baseline
    data["5m"][-1]["high"] += 1
    assert features_from_frames(data, NOW) is None
    assert not analyze_synthetic(data, NOW)["eligible"]


def test_missing_recent_candle_and_stale_higher_timeframe_fail_closed():
    data = frames()
    del data["5m"][-3]
    assert features_from_frames(data, NOW) is None
    assert not analyze_synthetic(data, NOW)["eligible"]
    data = frames()
    data["1h"].pop()
    assert features_from_frames(data, NOW) is None
    assert any("1h: closed-candle history is stale" in item for item in analyze_synthetic(data, NOW)["blockers"])


def test_chart_overlays_use_causal_lines_and_exclude_unwarmed_bands():
    candles = frames()["5m"]
    overlays = chart_overlays(candles)
    assert set(overlays) == {"frama", "frama_upper", "frama_lower", "range_filter", "signals"}
    assert len(overlays["frama_upper"]) == len(candles) - 199
    lines = {key: value for key, value in overlays.items() if key != "signals"}
    assert all(set(row) == {"epoch", "value"} for series in lines.values() for row in series)
    assert all(math.isfinite(row["value"]) for series in lines.values() for row in series)
    assert all(mark["source"] in {"FRAMA", "Range Filter"} and mark["side"] in {"BUY", "SELL"} for mark in overlays["signals"])


def test_pivot_is_only_known_after_two_right_hand_bars_close():
    data = frames()
    data["5m"][-3]["high"] += 20
    at_pivot = features_from_frames(data, NOW - 600)
    before_confirmation = features_from_frames(data, NOW - 300)
    at_confirmation = features_from_frames(data, NOW)
    assert at_pivot is not None and before_confirmation is not None and at_confirmation is not None
    # The deliberately tall pivot cannot become resistance until its confirming
    # candle closes; its distance suddenly becomes much greater at that point.
    assert before_confirmation["resistance_distance_atr"] < 2
    assert at_confirmation["resistance_distance_atr"] > 5
    changed_future = copy.deepcopy(data)
    changed_future["5m"][-1]["high"] += 50
    assert features_from_frames(changed_future, NOW - 300) == before_confirmation


def test_empty_history_is_no_trade_and_json_safe():
    result = analyze_synthetic({}, NOW)
    assert result["direction"] == "NO TRADE"
    assert result["decision_epoch"] is None
    assert not result["eligible"]
    assert features_from_frames({}, NOW) is None
    assert feature_table({}, NOW).empty
    json.dumps(result, allow_nan=False)
