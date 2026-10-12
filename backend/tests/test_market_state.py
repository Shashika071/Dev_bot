"""Stable multi-timeframe trend state."""

from app.signal_engine.market_state import (
    MarketStateConfig,
    MarketStateTracker,
    direction_blocked_by_trend,
    timeframe_strength,
)


def _trend(n=40, step=1.0):
    rows = []
    for i in range(n):
        price = 100 + i * step
        rows.append({
            "open": price - 0.2,
            "high": price + 0.4,
            "low": price - 0.5,
            "close": price,
            "epoch": 1_700_000_000 + i * 300,
        })
    return rows


def _tracker():
    return MarketStateTracker(
        config=MarketStateConfig(enter=0.35, exit=0.12, confirm_bars=2),
        path=None,
    )


def test_timeframe_strength_follows_a_clear_move():
    assert timeframe_strength(_trend(step=1.0)) > 0.2
    assert timeframe_strength(_trend(step=-1.0)) < -0.2


def test_small_score_stays_neutral_and_confirmation_is_required():
    tracker = _tracker()
    first = tracker.update(0.05, conflicted=False, bar_id=1, fresh=True)
    second = tracker.update(0.05, conflicted=False, bar_id=2, fresh=True)
    assert first.state == "NEUTRAL"
    assert second.state == "NEUTRAL"

    pending = tracker.update(0.8, conflicted=False, bar_id=3, fresh=True)
    confirmed = tracker.update(0.8, conflicted=False, bar_id=4, fresh=True)
    assert pending.state == "NEUTRAL"
    assert confirmed.state == "UPPER"
    assert confirmed.confirmed_strong


def test_same_bar_and_direct_reversal_do_not_flip():
    tracker = _tracker()
    tracker.update(0.8, conflicted=False, bar_id=1, fresh=True)
    upper = tracker.update(0.8, conflicted=False, bar_id=2, fresh=True)
    assert upper.state == "UPPER"
    same_bar = tracker.update(-0.9, conflicted=False, bar_id=2, fresh=True)
    assert same_bar.state == "UPPER"
    first_down = tracker.update(-0.9, conflicted=False, bar_id=3, fresh=True)
    second_down = tracker.update(-0.9, conflicted=False, bar_id=4, fresh=True)
    assert first_down.state == "UPPER"
    assert second_down.state == "NEUTRAL"
    assert second_down.state != "LOWER"


def test_tick_disagreement_damps_score_without_flipping_direction():
    from app.signal_engine.market_state import fuse_trend_scores

    rising = _trend(step=1.0)
    score, conflicted, parts = fuse_trend_scores(rising, rising, rising, tick_return=-0.02)
    assert parts["5m"] > 0
    assert score > 0
    assert conflicted is True


def test_closed_bar_state_flips_less_than_sign_changes():
    """Same closed bar cannot flip, and a noisy uptrend cannot jump UPPER to LOWER."""
    tracker = _tracker()
    states = []
    # One hour of repeated reads of the same closed bar, with a score that chatters.
    for i, score in enumerate([0.8, -0.8, 0.8, -0.2, 0.9, -0.9] * 10):
        states.append(tracker.update(score, conflicted=False, bar_id=1, fresh=True).state)
    assert states.count("UPPER") + states.count("LOWER") + states.count("NEUTRAL") == len(states)
    assert set(states) == {"NEUTRAL"}

    # Two hours of new 5-minute bars. Noise changes the raw sign, not the confirmed state.
    raw_sign = []
    previous = None
    direct_reversal = False
    for i in range(24):
        score = 0.6 if i % 2 == 0 else -0.6
        raw_sign.append(1 if score > 0 else -1)
        snap = tracker.update(score, conflicted=False, bar_id=100 + i, fresh=True)
        if previous in ("UPPER", "LOWER") and snap.state in ("UPPER", "LOWER") and snap.state != previous:
            direct_reversal = True
        previous = snap.state
    raw_flips = sum(1 for a, b in zip(raw_sign, raw_sign[1:]) if a != b)
    assert raw_flips >= 20
    assert direct_reversal is False
    assert tracker.state != "LOWER"


def test_conflict_and_stale_state_do_not_veto():
    tracker = _tracker()
    tracker.update(0.4, conflicted=True, bar_id=1, fresh=True)
    conflicted = tracker.update(0.4, conflicted=True, bar_id=2, fresh=True)
    assert conflicted.state == "CONFLICTED"
    assert direction_blocked_by_trend("lower", conflicted) == (True, "mixed_timeframes")
    assert direction_blocked_by_trend("upper", conflicted) == (True, "mixed_timeframes")

    tracker.update(0.8, conflicted=False, bar_id=3, fresh=True)
    upper = tracker.update(0.8, conflicted=False, bar_id=4, fresh=True)
    assert direction_blocked_by_trend("lower", upper)[0] is True
    assert direction_blocked_by_trend("upper", upper)[0] is False
    stale = tracker.update(0.8, conflicted=False, bar_id=None, fresh=False)
    assert direction_blocked_by_trend("lower", stale)[0] is False
