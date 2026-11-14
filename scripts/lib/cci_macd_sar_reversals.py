"""Replay the fixed-unit market orders from v100-cci-macd-sar.pine."""

from decimal import Decimal


def replay_reversals(candles, signals):
    """Return point P/L for one-unit positions reversed at the next M1 open.

    Each signal is (index of the confirmed candle, 'RISE' or 'FALL').
    A gap ends the segment. An open position is marked at that segment's
    final close. Its unrealized P/L stays separate from closed trades.
    Fees and slippage are zero. Prices are in index points.
    """
    signal_map = {}
    for index, side in signals:
        if not isinstance(index, int) or not 0 <= index < len(candles):
            raise ValueError(f"Invalid candle index {index}")
        if side not in ("RISE", "FALL") or index in signal_map:
            raise ValueError(f"Invalid or duplicate signal at index {index}")
        signal_map[index] = side

    segments = fills = ignored = unfilled = 0
    wins = losses = flats = 0
    profit = loss = Decimal(0)
    open_positions = []
    position = None
    segment_start = 0
    for i, candle in enumerate(candles):
        timestamp = candle["time"]
        if i and timestamp <= candles[i - 1]["time"]:
            raise ValueError("Candles must be strictly chronological")
        if i == segment_start:
            segments += 1
        last_in_segment = i + 1 == len(candles) or candles[i + 1]["time"] != timestamp + 60

        side = signal_map.get(i)
        # Pyramiding is zero. A repeated direction produces no additional order.
        if side and position and position["side"] == side:
            ignored += 1
        elif side and last_in_segment:
            unfilled += 1
        elif side:
            next_bar = candles[i + 1]
            fill = Decimal(str(next_bar["open"]))
            if position is not None:
                change = (fill - position["entry"]) * position["direction"]
                if change > 0:
                    profit += change
                    wins += 1
                elif change < 0:
                    loss -= change
                    losses += 1
                else:
                    flats += 1
            position = {
                "side": side,
                "direction": 1 if side == "RISE" else -1,
                "entry_time": next_bar["time"],
                "entry": fill,
            }
            fills += 1

        if last_in_segment:
            if position is not None:
                mark = Decimal(str(candle["close"]))
                unrealized = (mark - position["entry"]) * position["direction"]
                open_positions.append({
                    "segment_start": candles[segment_start]["time"],
                    "side": position["side"],
                    "entry_time": position["entry_time"],
                    "entry_price": float(position["entry"]),
                    "mark_time": timestamp + 60,
                    "mark_price": float(mark),
                    "unrealized_points": float(unrealized),
                })
            position = None
            segment_start = i + 1

    closed = wins + losses + flats
    assert closed + len(open_positions) == fills
    assert fills + ignored + unfilled == len(signal_map)
    return {
        "segments": segments,
        "signals": len(signal_map),
        "fills": fills,
        "same_side_signals_ignored": ignored,
        "unfilled_signals": unfilled,
        "closed_trades": closed,
        "winning_trades": wins,
        "losing_trades": losses,
        "flat_trades": flats,
        "win_rate": wins / closed if closed else None,
        "loss_rate": losses / closed if closed else None,
        "flat_rate": flats / closed if closed else None,
        "gross_profit_points": float(profit),
        "gross_loss_points": float(loss),
        "net_realized_points": float(profit - loss),
        "open_positions": open_positions,
    }
