"""Check closed candles without inventing the missing ones."""
from __future__ import annotations

import math


def _finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _valid_candle(row: dict, step: int, now: int) -> dict | None:
    if not isinstance(row, dict):
        return None
    epoch = row.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or not float(epoch).is_integer():
        return None
    epoch = int(epoch)
    if epoch < 0 or epoch % step != 0 or epoch + step > now:
        return None
    prices = [_finite(row.get(name)) for name in ("open", "high", "low", "close")]
    if any(price is None or price <= 0 for price in prices):
        return None
    open_, high, low, close = prices
    if low > min(open_, close, high) or high < max(open_, close, low):
        return None
    return {"epoch": epoch, "open": open_, "high": high, "low": low, "close": close}


def assess_candles(rows: list, step: int, now: int) -> dict:
    """Keep the first valid bar at each epoch. Gaps stay empty."""
    if not isinstance(rows, (list, tuple)):
        rows = []
    kept: dict[int, dict] = {}
    invalid = 0
    duplicates = 0
    for row in rows:
        candle = _valid_candle(row, step, now)
        if candle is None:
            invalid += 1
            continue
        if candle["epoch"] in kept:
            duplicates += 1
            continue
        kept[candle["epoch"]] = candle
    epochs = sorted(kept)
    gaps = 0
    missing_bars = 0
    for earlier, later in zip(epochs, epochs[1:]):
        if later - earlier > step:
            gaps += 1
            missing_bars += (later - earlier) // step - 1
    blocks = gaps > 0 or invalid > 0
    if blocks:
        reason = "Candle history has gaps or rejected rows, so missing prices were not filled in."
    elif not epochs:
        reason = "No valid closed candles are available."
    else:
        reason = "Closed candles are contiguous."
    return {
        "candles": [kept[epoch] for epoch in epochs],
        "gaps": gaps,
        "missing_bars": missing_bars,
        "duplicates": duplicates,
        "invalid": invalid,
        "blocks_signal": blocks or not epochs,
        "reason": reason,
    }
