import copy
import json
import math

import pytest

from app.forex.analysis import INTERVALS, analyze_forex


NOW = 1_792_396_800


def history(step, *, direction=1, count=100, breakout=False):
    end = NOW // step * step
    rows = []
    # Rising swing structure with sufficient counter-moves to exercise RSI.
    for index in range(count):
        close = 1.1 + direction * (index * 0.00015 + 0.00065 * math.sin(index * 0.7))
        open_ = close - direction * 0.0001
        rows.append({
            "epoch": end - (count - index) * step,
            "open": open_, "close": close,
            "high": max(open_, close) + 0.00015,
            "low": min(open_, close) - 0.00015,
        })
    if breakout:
        last = rows[-1]
        high = max(row["high"] for row in rows[-21:-1])
        low = min(row["low"] for row in rows[-21:-1])
        if direction == 1:
            last.update(open=high - 0.00015, low=high - 0.0002, close=high + 0.00025, high=high + 0.0003)
        else:
            last.update(open=low + 0.00015, high=low + 0.0002, close=low - 0.00025, low=low - 0.0003)
    return rows


def dataset(direction=1):
    return {name: history(step, direction=direction, breakout=name == "15m") for name, step in INTERVALS.items()}


@pytest.mark.parametrize("direction,expected", [(1, "RISE"), (-1, "FALL")])
def test_trend_and_breakout_agreement_produces_mirrored_candidates(direction, expected):
    result = analyze_forex(dataset(direction), symbol="frxEURUSD", now=NOW)
    assert result["direction"] == expected, result["blockers"]
    assert result["ready"]
    assert result["score"] >= 80
    assert result["setup"] == "range_breakout"
    assert result["score_type"].endswith("not a win probability")
    assert result["duration_seconds"] == 14400
    assert len(result["score_groups"]) == 5
    assert sum(group["points"] for group in result["score_groups"].values()) == result["score"]
    json.dumps(result, allow_nan=False)


def test_future_and_forming_bars_cannot_change_any_evidence():
    data = dataset()
    baseline = analyze_forex(data, symbol="frxEURUSD", now=NOW + 17)
    for name, step in INTERVALS.items():
        for offset in (0, step, 10 * step):
            data[name].append({"epoch": NOW // step * step + offset, "open": 5, "high": 100, "low": 0.01, "close": 99})
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW + 17)
    assert result == baseline
    assert all(row["epoch"] + 900 <= NOW + 17 for row in result["candles"])


def test_all_future_bars_do_not_fall_back_to_unclosed_history():
    data = dataset()
    for name, step in INTERVALS.items():
        for index, row in enumerate(data[name]):
            row["epoch"] = NOW + index * step
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert result["direction"] == "WAIT"
    assert not result["ready"]
    assert result["candles"] == []
    assert all(item["closed_bars"] == 0 for item in result["timeframes"].values())
    assert len(result["blockers"]) == 6


def test_exact_close_boundary_counts_closed_bar():
    data = dataset()
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert result["timeframes"]["15m"]["last_closed_epoch"] == NOW - 900
    assert result["timeframes"]["15m"]["closed_at"] == NOW


@pytest.mark.parametrize("name", list(INTERVALS))
def test_every_required_frame_must_have_60_closed_bars(name):
    data = dataset()
    data[name] = data[name][-59:]
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert result["direction"] == "WAIT"
    assert any("60 valid closed candles (received 59)" in item for item in result["blockers"])


def test_stale_data_is_a_blocker_even_when_other_frames_agree():
    data = dataset()
    data["1m"] = data["1m"][:-5]
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert result["timeframes"]["1m"]["stale"]
    assert "M1 closed-candle data is stale." in result["blockers"]


def test_conflicting_daily_bias_is_wait():
    data = dataset()
    data["1d"] = history(86400, direction=-1)
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert result["direction"] == "WAIT"
    assert result["bias"] == "WAIT"
    assert any("do not agree" in item for item in result["blockers"])


def test_low_timeframes_do_not_overrule_long_term_direction():
    data = dataset()
    baseline = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    data["1m"] = history(60, direction=-1)
    data["5m"] = history(300, direction=-1)
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert result["direction"] == baseline["direction"] == "RISE"
    assert result["score"] == baseline["score"]
    assert result["timeframes"]["1m"]["role"] == "context"


def test_factors_cover_checklist_and_do_not_claim_order_or_volume_data():
    result = analyze_forex(dataset(), symbol="frxEURUSD", now=NOW)
    factors = {item["id"]: item for item in result["factors"]}
    assert set(factors) == set(range(1, 21)) | {29}
    assert all(set(item) == {"id", "name", "status", "detail"} for item in factors.values())
    assert factors[4]["status"] == factors[5]["status"] == "heuristic"
    assert factors[20]["status"] == factors[29]["status"] == "unavailable"
    assert "not observed" in factors[4]["detail"]
    assert "ATR" in factors[3]["detail"] or "No repeated H1" in factors[3]["detail"]
    assert "false" in factors[7]["detail"].lower() or "False" in factors[7]["detail"] or "No M15 false" in factors[7]["detail"]
    assert "Fibonacci" in factors[19]["detail"] or "fibonacci" in factors[19]["detail"].lower() or "withheld" in factors[19]["detail"]
    assert all("confidence" not in item for item in result["events"])


def test_supplied_tick_volume_is_only_activity_context():
    data = dataset()
    baseline = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    for row in data["15m"]:
        row["tick_volume"] = 33
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert next(item for item in result["factors"] if item["id"] == 20)["status"] == "observed"
    assert result["score"] == baseline["score"]


def test_interval_aliases_and_sorting_are_deterministic():
    data = dataset()
    baseline = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    aliases = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"}
    alias_data = {aliases[name]: list(reversed(rows)) + [copy.deepcopy(rows[-1])] for name, rows in data.items()}
    assert analyze_forex(alias_data, symbol="frxEURUSD", now=NOW) == baseline
    seconds_data = {INTERVALS[name]: rows for name, rows in data.items()}
    assert analyze_forex(seconds_data, symbol="frxEURUSD", now=NOW) == baseline


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -1, "bad"])
def test_invalid_ohlc_blocks_and_never_leaks_nan(bad_value):
    data = dataset()
    data["1h"][-1]["close"] = bad_value
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert any("malformed" in item for item in result["blockers"])
    json.dumps(result, allow_nan=False)


def test_conflicting_duplicate_closed_candles_block():
    data = dataset()
    conflicting = copy.deepcopy(data["15m"][-1])
    conflicting["close"] -= 0.00001
    data["15m"].append(conflicting)
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert any("conflicting" in item for item in result["blockers"])


def test_flat_market_waits_with_finite_output():
    data = dataset()
    for rows in data.values():
        for row in rows:
            row.update(open=1.1, high=1.1, low=1.1, close=1.1)
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert result["direction"] == "WAIT"
    assert any("ADX" in item for item in result["blockers"])
    json.dumps(result, allow_nan=False)


def test_extreme_trigger_bar_is_rejected():
    data = dataset()
    data["15m"][-1]["high"] += 0.02
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert result["volatility"]["extreme"]


def test_trend_alone_cannot_replace_a_price_action_trigger():
    data = dataset()
    close = data["15m"][-2]["close"]
    data["15m"][-1].update(open=close, high=close + 0.00001, low=close - 0.00001, close=close)
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert result["bias"] == "RISE"
    assert result["setup"] is None
    assert result["direction"] == "WAIT"
    assert any("No credible" in item for item in result["blockers"])


def test_latest_failed_upside_breakout_blocks_rise_candidate():
    data = dataset()
    prior_high = max(row["high"] for row in data["15m"][-21:-1])
    data["15m"][-1].update(open=prior_high - 0.00005, high=prior_high + 0.0003, low=prior_high - 0.0002, close=prior_high - 0.0001)
    result = analyze_forex(data, symbol="frxEURUSD", now=NOW)
    assert not result["ready"]
    assert result["direction"] == "WAIT"
    assert any("failed breakout" in item for item in result["blockers"])
    assert next(item for item in result["factors"] if item["id"] == 7)["status"] == "blocked"


def test_short_expiry_is_not_silently_accepted():
    result = analyze_forex(dataset(), symbol="frxEURUSD", now=NOW, duration_seconds=60)
    assert not result["ready"]
    assert result["direction"] == "WAIT"
    assert any("four-hour expiry" in item for item in result["blockers"])
