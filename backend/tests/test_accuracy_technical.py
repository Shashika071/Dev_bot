"""Technical inputs are available only after their information is observable."""

import numpy as np
import pytest

from app.accuracy.technical import (
    FEATURE_GROUPS,
    FEATURE_NAMES,
    HISTORY_SECONDS,
    MIN_HISTORY_SECONDS,
    TechnicalContext,
    _confirmed_structure,
    _timeframe_features,
    technical_features,
)


def ticks(hours=14, *, flat=False):
    t = np.arange(0, hours * 3600 + 2, 2, dtype=np.int64)
    p = np.full(len(t), 100.) if flat else 100. + .00003*t + .7*np.sin(t/160.) + .3*np.sin(t/37.)
    return t, p


def test_flat_prices_have_finite_neutral_indicators_and_all_nine_groups():
    t, p = ticks(flat=True)
    got = technical_features(t, p, 12*3600)
    assert got is not None
    assert tuple(got) == FEATURE_NAMES
    assert len(FEATURE_GROUPS) == 9
    assert np.isfinite(list(got.values())).all()
    assert all(any(name.startswith(prefix) for name in got) for prefixes in FEATURE_GROUPS.values() for prefix in prefixes)
    for tf in ("1m", "5m", "15m"):
        assert got[f"tech_indicator_{tf}_rsi"] == 50.
        assert got[f"tech_indicator_{tf}_adx"] == 0.
        assert got[f"tech_indicator_{tf}_atr"] == 0.
        assert got[f"tech_trend_{tf}_ema_gap_atr"] == pytest.approx(0., abs=1e-5)
        assert got[f"tech_candle_{tf}_close_position"] == 0.
    assert got["tech_mtf_signed_alignment"] == 0.
    assert not any(name.startswith("tech_indicator_1h_") for name in got)


def test_future_changes_and_an_unfinished_candle_do_not_change_features():
    t, p = ticks()
    decision = 12*3600+17
    baseline = TechnicalContext(t, p).snapshot(decision)
    assert baseline is not None
    mutated = p.copy()
    # This includes already-observed ticks of the open 1m candle. Technical
    # candle features intentionally exclude the whole unfinished candle.
    mutated[t >= (decision//60)*60] += 500.
    assert TechnicalContext(t, mutated).snapshot(decision) == baseline
    # Appending the tick on a candle boundary makes the prior candle available,
    # but the new tick belongs to the next candle and cannot alter its OHLC.
    decision = 12*3600
    prefix = t < decision
    left = TechnicalContext(t[prefix], p[prefix]).snapshot(decision)
    with_boundary = t <= decision
    right = TechnicalContext(t[with_boundary], p[with_boundary]).snapshot(decision)
    assert right == left


def test_offline_context_matches_live_bounded_window():
    t, p = ticks()
    decision = 13*3600+31
    offline = TechnicalContext(t, p).snapshot(decision)
    window = (t >= decision-HISTORY_SECONDS) & (t <= decision)
    assert technical_features(t[window], p[window], decision) == offline


def test_candle_only_becomes_available_at_close():
    t, p = ticks()
    edge = 12*3600
    changed = p.copy()
    changed[(t >= edge-60) & (t < edge)] += 2.
    before = edge-1
    assert TechnicalContext(t, p).snapshot(before) == TechnicalContext(t, changed).snapshot(before)
    assert TechnicalContext(t, p).snapshot(edge) != TechnicalContext(t, changed).snapshot(edge)


def test_warmup_freshness_and_gap_recovery_are_explicit():
    t, p = ticks()
    ctx = TechnicalContext(t, p)
    assert ctx.snapshot(MIN_HISTORY_SECONDS-1) is None
    assert ctx.snapshot(MIN_HISTORY_SECONDS) is not None
    assert ctx.snapshot(int(t[-1])+6) is None
    # A 20-second outage inside the recent ten-hour warmup invalidates data.
    keep = (t < 3*3600) | (t >= 3*3600+20)
    gapped = TechnicalContext(t[keep], p[keep])
    assert gapped.snapshot(12*3600) is None
    # After ten continuous hours, an older gap simply resets history.
    assert gapped.snapshot(14*3600) is not None


def test_swings_are_confirmed_after_two_right_bars_and_not_repainted():
    high = np.array([2.,3.,6.,4.,3.,4.,8.])
    low = np.array([1.,1.5,2.,2.5,2.,2.5,3.])
    close = (high+low)/2
    assert _confirmed_structure(high[:4], low[:4], close[:4])["has_swing_high"] == 0.
    confirmed = _confirmed_structure(high[:5], low[:5], close[:5])
    assert confirmed["has_swing_high"] == 1.
    assert confirmed["bars_since_swing_high"] == 2.
    # A higher high later does not retroactively remove the earlier feature.
    assert _confirmed_structure(high[:5], low[:5], close[:5]) == confirmed


def test_bos_uses_a_previously_confirmed_level():
    high = np.array([2.,3.,6.,4.,3.,4.,8.])
    low = np.array([1.,1.5,2.,2.5,2.,2.5,3.])
    close = np.array([1.5,2.,5.,3.,2.5,3.,7.])
    result = _confirmed_structure(high, low, close)
    assert result["bos"] == 1.
    assert result["bars_since_bos"] == 0.


def test_choch_marks_a_break_against_confirmed_higher_high_higher_low_structure():
    close = np.array([5.,6.,10.,6.,5.,6.,12.,7.,6.,7.,13.,8.,7.,8.,14.,9.,8.,9.,15.,5.])
    high, low = close+.2, close-.2
    before = _confirmed_structure(high[:-1], low[:-1], close[:-1])
    assert before["state"] == 1.
    assert before["higher_high"] == before["higher_low"] == 1.
    reversed_ = _confirmed_structure(high, low, close)
    assert reversed_["bos"] == reversed_["choch"] == -1.
    assert reversed_["bars_since_choch"] == 0.


@pytest.mark.parametrize("direction,rsi", [(1.,100.), (-1.,0.)])
def test_one_way_prices_have_correct_rsi_extremes(direction, rsi):
    t = np.arange(0, 12*3600+2, 2, dtype=np.int64)
    p = 100. + direction * t * .0001
    result = technical_features(t, p, 12*3600)
    assert result is not None
    for tf in ("1m", "5m", "15m", "1h"):
        if tf != "1h":
            assert result[f"tech_indicator_{tf}_rsi"] == rsi
        assert direction * result[f"tech_trend_{tf}_ema_gap_atr"] > 0


def test_range_sweep_requires_rejection_while_breakout_requires_close_outside():
    open_ = np.full(30,100.)
    high = np.full(30,101.)
    low = np.full(30,99.)
    close = np.full(30,100.)
    high[-1] = 102.
    swept,_ = _timeframe_features("1m",open_,high,low,close)
    assert swept["tech_sweep_1m_upper_range_rejection"] == 1.
    assert swept["tech_breakout_1m_upper_close_break"] == 0.
    close[-1] = 101.5
    broken,_ = _timeframe_features("1m",open_,high,low,close)
    assert broken["tech_sweep_1m_upper_range_rejection"] == 0.
    assert broken["tech_breakout_1m_upper_close_break"] == 1.


@pytest.mark.parametrize("bad", ["duplicate", "nan", "negative", "fractional"])
def test_invalid_tick_inputs_fail_explicitly(bad):
    t, p = ticks()
    if bad == "duplicate":
        t[1] = t[0]
    elif bad == "nan":
        p[1] = np.nan
    elif bad == "negative":
        p[1] = -1
    else:
        t = t.astype(float)
        t[1] += .5
    with pytest.raises(ValueError):
        TechnicalContext(t, p)
