"""Donchian and range breakout / retest lifecycle."""

from __future__ import annotations

from app.analysis.indicators import frame, last_atr
from app.features.indicators import donchian_channel


def breakout_events(candles: list[dict], period: int = 20) -> list[dict]:
    df = frame(candles)
    if len(df) < period + 3:
        return []
    upper, lower = donchian_channel(df["high"], df["low"], period)
    atr = max(last_atr(candles), 1e-9)
    events = []
    for index in range(period + 1, len(df)):
        close = float(df.iloc[index]["close"])
        prev_close = float(df.iloc[index - 1]["close"])
        up = upper.iloc[index]
        down = lower.iloc[index]
        if up != up or down != down:
            continue
        epoch = int(df.iloc[index]["epoch"])
        if prev_close <= float(up) and close > float(up):
            events.append(_break("donchian_breakout", "upper", epoch, float(up), close))
        elif prev_close >= float(down) and close < float(down):
            events.append(_break("donchian_breakout", "lower", epoch, float(down), close))
        if events:
            last = events[-1]
            if last["type"] == "donchian_breakout" and index > 0:
                level = float(last["geometry"]["level"])
                low = float(df.iloc[index]["low"])
                high = float(df.iloc[index]["high"])
                if last["direction"] == "upper" and low <= level + atr * 0.25 and close > level and epoch != last["epoch"]:
                    events.append(_break("breakout_retest", "upper", epoch, level, close))
                if last["direction"] == "lower" and high >= level - atr * 0.25 and close < level and epoch != last["epoch"]:
                    events.append(_break("breakout_retest", "lower", epoch, level, close))
    dedup = {}
    for event in events:
        dedup[event["id"]] = event
    return list(dedup.values())[-12:]


def _break(name: str, direction: str, epoch: int, level: float, close: float) -> dict:
    return {
        "id": f"{name}:{direction}:{epoch}",
        "type": name,
        "direction": direction,
        "epoch": epoch,
        "end_epoch": epoch,
        "confidence": 0.74 if name == "breakout_retest" else 0.7,
        "status": "confirmed",
        "geometry": {"level": level, "close": close},
        "invalidation": level,
        "evidence": f"{name.replace('_', ' ')} at {level:.4f}",
    }
