import pandas as pd

from app.strategies.registry import StrategyRegistry
from app.strategies.touch_confluence import TouchConfluenceStrategy


def _features(direction: str, reachable: bool = True) -> pd.DataFrame:
    sign = 1.0 if direction == "upper" else -1.0
    return pd.DataFrame({
        "return_10": [0.001 * sign],
        "return_60": [0.002 * sign],
        "return_300": [0.003 * sign],
        "price_range_60": [0.12 if reachable else 0.01],
        "price_range_300": [0.25 if reachable else 0.02],
        "ma_crossover": [sign],
        "ma_fast_slope": [0.002 * sign],
        "ma_slow_slope": [0.001 * sign],
        "rsi_14": [62.0 if direction == "upper" else 38.0],
        "bb_position": [0.72 if direction == "upper" else 0.28],
        "donchian_breakout_state": [sign],
        "realized_vol_60": [0.001],
    })


def test_upper_touch_confluence():
    result = TouchConfluenceStrategy().evaluate(
        _features("upper"), current_price=100.0, barrier_distance=0.09
    )
    assert result is not None
    assert result.direction == "upper"
    assert result.strategy_name == "touch_confluence"
    assert result.features["reachability_ratio"] > 1


def test_lower_touch_confluence():
    result = TouchConfluenceStrategy().evaluate(
        _features("lower"), current_price=100.0, barrier_distance=0.09
    )
    assert result is not None
    assert result.direction == "lower"


def test_rejects_unreachable_barrier():
    result = TouchConfluenceStrategy().evaluate(
        _features("upper", reachable=False),
        current_price=100.0,
        barrier_distance=0.09,
    )
    assert result is None


def test_registered_by_default():
    registry = StrategyRegistry()
    registry.register_defaults()
    assert registry.get_strategy("touch_confluence") is not None
