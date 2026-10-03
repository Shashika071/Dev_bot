"""Barrier direction helpers for One-Touch contracts."""

from typing import Iterable


def barrier_magnitude(barrier_input: str) -> str:
    """Strip sign from a barrier string, e.g. '+0.09' / '-0.09' -> '0.09'."""
    return str(barrier_input).strip().lstrip("+-")


def barrier_for_direction(barrier_input: str, direction: str) -> str:
    """Return signed barrier for upper (+) or lower (-)."""
    mag = barrier_magnitude(barrier_input)
    if direction == "lower":
        return f"-{mag}"
    return f"+{mag}"


def configured_directions(barrier_direction: str | None) -> list[str]:
    """
    Expand settings direction into concrete directions to monitor.
    Accepts: upper | lower | both
    """
    d = (barrier_direction or "both").strip().lower()
    if d == "both":
        return ["upper", "lower"]
    if d in ("upper", "lower"):
        return [d]
    return ["upper", "lower"]


def iter_direction_barriers(
    barrier_input: str, barrier_direction: str | None
) -> Iterable[tuple[str, str]]:
    """Yield (direction, signed_barrier) pairs for the configured mode."""
    for direction in configured_directions(barrier_direction):
        yield direction, barrier_for_direction(barrier_input, direction)
