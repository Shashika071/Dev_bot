"""Both-direction barrier and feature routing helpers."""

from app.deriv.barriers import (
    barrier_for_direction,
    configured_directions,
    iter_direction_barriers,
)


def test_configured_directions_both():
    assert configured_directions("both") == ["upper", "lower"]
    assert configured_directions("upper") == ["upper"]
    assert configured_directions("lower") == ["lower"]


def test_barrier_for_direction_signs():
    assert barrier_for_direction("0.09", "upper").startswith("+") or float(
        barrier_for_direction("0.09", "upper")
    ) > 0
    lower = barrier_for_direction("0.09", "lower")
    assert float(lower) < 0 or str(lower).startswith("-")


def test_iter_direction_barriers_both():
    pairs = list(iter_direction_barriers("0.09", "both"))
    dirs = [d for d, _ in pairs]
    assert dirs == ["upper", "lower"]
    assert float(pairs[0][1]) > 0
    assert float(pairs[1][1]) < 0
