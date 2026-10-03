"""Confidence-gated manual signals vs blind force."""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.signal_engine.daily_cap import DailyCapManager
from app.signal_engine.ev_filter import EVFilter
from app.signal_engine.generator import SignalGenerator
from app.signal_engine.lifecycle import SignalLifecycleManager
from app.strategies.registry import StrategyRegistry


class MagicModel:
    def __init__(self, p=0.78):
        self.p = p

    def predict_proba(self, X):
        import numpy as np

        return np.array([self.p])


class MagicSettings:
    barrier_direction = "upper"
    barrier_input = "0.09"
    barrier_unit_description = "relative_price_points"
    duration_seconds = 540


def _upper_confluence_features():
    return pd.DataFrame({
        "return_10": [0.001],
        "return_60": [0.002],
        "return_300": [0.003],
        "price_range_60": [0.12],
        "price_range_300": [0.25],
        "ma_crossover": [1.0],
        "ma_fast_slope": [0.002],
        "ma_slow_slope": [0.001],
        "rsi_14": [62.0],
        "bb_position": [0.72],
        "donchian_breakout_state": [1.0],
        "realized_vol_60": [0.001],
    })


def _gen(p=0.78, has_edge=False):
    strategies = StrategyRegistry()
    strategies.register_defaults()
    lifecycle = SignalLifecycleManager()
    lifecycle.create_signal = AsyncMock(return_value=MagicMock(signal_id="SIG-CONF001"))
    cap = DailyCapManager()
    cap.can_issue_signal = AsyncMock(return_value=(True, "ok"))
    cap.get_today_date = MagicMock(return_value="2026-04-01")
    cap.get_next_sequence = AsyncMock(return_value=1)
    gen = SignalGenerator(strategies, EVFilter(), cap, lifecycle)
    gen.set_model(
        model=MagicModel(p),
        calibrator=None,
        version_id=1,
        has_edge=has_edge,
        direction="upper",
    )
    return gen, lifecycle


@pytest.mark.asyncio
async def test_confidence_override_emits_when_high():
    gen, lifecycle = _gen(p=0.80)
    feat = _upper_confluence_features()
    session = AsyncMock()
    with patch(
        "app.signal_engine.generator.get_latest_confirmed_settings",
        new=AsyncMock(return_value=MagicSettings()),
    ):
        result = await gen.evaluate_and_generate(
            session=session,
            features_df=feat,
            current_price=100.0,
            barrier_distance=0.09,
            symbol="R_100",
            # ask/payout => breakeven 0.40; margin 0.40 >= 0.03
            current_quote={"ask_price": 4.0, "payout": 10.0, "barrier_resolved": 100.09},
            allowed_directions=["upper"],
            confidence_override=True,
            min_confidence=0.72,
            min_margin_over_breakeven=0.03,
        )
    assert result is not None
    assert result["confidence_override"] is True
    lifecycle.create_signal.assert_awaited()


@pytest.mark.asyncio
async def test_confidence_override_skips_when_low():
    gen, lifecycle = _gen(p=0.55)
    feat = _upper_confluence_features()
    session = AsyncMock()
    with patch(
        "app.signal_engine.generator.get_latest_confirmed_settings",
        new=AsyncMock(return_value=MagicSettings()),
    ):
        result = await gen.evaluate_and_generate(
            session=session,
            features_df=feat,
            current_price=100.0,
            barrier_distance=0.09,
            symbol="R_100",
            current_quote={"ask_price": 4.0, "payout": 10.0},
            allowed_directions=["upper"],
            confidence_override=True,
            min_confidence=0.72,
            min_margin_over_breakeven=0.03,
        )
    assert result is None
    lifecycle.create_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_confidence_override_requires_touch_confluence():
    gen, lifecycle = _gen(p=0.99)
    feat = pd.DataFrame({
        "return_10": [0.001],
        "price_range_60": [0.01],  # barrier is not reachable in recent range
        "price_range_300": [0.02],
    })
    session = AsyncMock()
    with patch(
        "app.signal_engine.generator.get_latest_confirmed_settings",
        new=AsyncMock(return_value=MagicSettings()),
    ):
        result = await gen.evaluate_and_generate(
            session=session,
            features_df=feat,
            current_price=100.0,
            barrier_distance=0.09,
            symbol="R_100",
            current_quote={"ask_price": 4.0, "payout": 10.0},
            allowed_directions=["upper"],
            confidence_override=True,
            min_confidence=0.95,
            min_margin_over_breakeven=0.03,
        )
    assert result is None
    lifecycle.create_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_override_no_edge_rejects():
    gen, lifecycle = _gen(p=0.90, has_edge=False)
    feat = pd.DataFrame({"return_10": [0.0]})
    session = AsyncMock()
    with patch(
        "app.signal_engine.generator.get_latest_confirmed_settings",
        new=AsyncMock(return_value=MagicSettings()),
    ):
        result = await gen.evaluate_and_generate(
            session=session,
            features_df=feat,
            current_price=100.0,
            barrier_distance=0.09,
            symbol="R_100",
            current_quote={"ask_price": 4.0, "payout": 10.0},
            allowed_directions=["upper"],
            force_no_edge=False,
            confidence_override=False,
        )
    assert result is None
    lifecycle.create_signal.assert_not_awaited()
