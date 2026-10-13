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


def touch_order_fields(direction: str, barrier: str, stake: float) -> dict:
    """Validate a manual One-Touch ticket. Returns the signed barrier and stake."""
    side = str(direction or "").strip().lower()
    if side not in ("upper", "lower"):
        raise ValueError("Touch trades only: choose upper or lower")
    magnitude = barrier_magnitude(barrier)
    try:
        distance = float(magnitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("Barrier must be a number") from exc
    if distance <= 0:
        raise ValueError("Barrier must be greater than 0")
    amount = round(float(stake), 2)
    if amount <= 0:
        raise ValueError("Stake must be greater than 0")
    if amount > 10000:
        raise ValueError("Stake is too large")
    return {
        "direction": side,
        "barrier": barrier_for_direction(magnitude, side),
        "stake": amount,
    }


_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def touch_duration(duration: int, unit: str) -> tuple[int, str]:
    """Validate the contract length chosen on the chart ticket."""
    code = str(unit or "m").strip().lower()
    if code not in _DURATION_UNITS:
        raise ValueError("Duration unit must be seconds, minutes, hours, or days")
    try:
        value = int(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("Duration must be a whole number") from exc
    if value <= 0:
        raise ValueError("Duration must be greater than 0")
    if value > 1440:
        raise ValueError("Duration is too long")
    return value, code


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
