"""Exact Touch contract identity for training, scoring, and execution."""

from __future__ import annotations

from typing import Any


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 1e-9


def model_matches_contract(
    metadata: dict | None,
    *,
    symbol: str | None = None,
    direction: str | None = None,
    barrier_distance: float | None = None,
    duration_seconds: int | None = None,
    entry_delay_seconds: int | None = None,
    barrier_unit: str | None = None,
) -> tuple[bool, str]:
    """Return whether metadata is the contract being traded.

    Empty metadata is treated as a legacy in-process test double. Once a field
    is present it must match; a probability from another contract is not reused.
    """
    meta: dict[str, Any] = metadata or {}
    if not meta:
        return True, "no_metadata"
    if symbol is not None and meta.get("symbol") and str(meta["symbol"]) != str(symbol):
        return False, f"symbol {meta.get('symbol')} != {symbol}"
    if direction is not None and meta.get("direction") and str(meta["direction"]) != str(direction):
        return False, f"direction {meta.get('direction')} != {direction}"
    if barrier_distance is not None and meta.get("barrier_distance") is not None:
        if not _close(meta["barrier_distance"], barrier_distance):
            return False, (
                f"barrier {meta.get('barrier_distance')} != {barrier_distance}"
            )
    if duration_seconds is not None and meta.get("duration_seconds") is not None:
        if int(meta["duration_seconds"]) != int(duration_seconds):
            return False, (
                f"duration {meta.get('duration_seconds')} != {duration_seconds}"
            )
    if entry_delay_seconds is not None and meta.get("entry_delay_seconds") is not None:
        if int(meta["entry_delay_seconds"]) != int(entry_delay_seconds):
            return False, (
                f"entry_delay {meta.get('entry_delay_seconds')} != {entry_delay_seconds}"
            )
    if barrier_unit is not None and meta.get("barrier_unit"):
        if str(meta["barrier_unit"]) != str(barrier_unit):
            return False, f"barrier_unit {meta.get('barrier_unit')} != {barrier_unit}"
    return True, "match"
