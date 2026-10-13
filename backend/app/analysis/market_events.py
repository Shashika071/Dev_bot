"""Structure events: BOS, CHoCH, sweeps, false breaks, spikes, and pullbacks.

Swings are confirmed only after the bars to their right have closed.
Moves smaller than the ATR noise band do not create a break event.
"""

from __future__ import annotations


def label_swings(points: list[dict], atr: float, noise_multiple: float = 0.6) -> list[dict]:
    """Tag each swing HH/HL/LH/LL and mark sub-ATR turns as filtered noise."""
    last_price = {"high": None, "low": None}
    labeled = []
    band = max(float(atr), 1e-9) * noise_multiple
    for point in points:
        item = dict(point)
        kind = point["kind"]
        previous = last_price[kind]
        if previous is None:
            item["label"] = "H" if kind == "high" else "L"
            item["filtered"] = False
        else:
            delta = float(point["price"]) - float(previous)
            item["filtered"] = abs(delta) < band
            if kind == "high":
                item["label"] = "HH" if delta > 0 else "LH"
            else:
                item["label"] = "HL" if delta > 0 else "LL"
        if not item["filtered"]:
            last_price[kind] = float(point["price"])
        labeled.append(item)
    return labeled


def _event(name: str, direction: str, candle: dict, price: float, evidence: str, **geometry) -> dict:
    epoch = int(candle["epoch"])
    return {
        "id": f"{name}:{direction}:{epoch}",
        "type": name,
        "direction": direction,
        "epoch": epoch,
        "end_epoch": epoch,
        "confidence": 0.75,
        "status": "confirmed",
        "geometry": {"price": price, **geometry},
        "invalidation": price,
        "evidence": evidence,
    }


def price_action_events(
    candles: list[dict],
    points: list[dict],
    atr: float,
    *,
    right: int = 3,
) -> dict:
    labeled = label_swings(points, atr)
    events: list[dict] = []
    for point in labeled:
        events.append({
            "id": f"swing_{point['kind']}:{point['epoch']}",
            "type": f"swing_{point['kind']}",
            "direction": "lower" if point["kind"] == "high" else "upper",
            "epoch": int(point["epoch"]),
            "end_epoch": int(point["epoch"]),
            "confidence": 0.4 if point.get("filtered") else 0.7,
            "status": "filtered" if point.get("filtered") else "confirmed",
            "geometry": {"price": float(point["price"]), "label": point.get("label")},
            "evidence": (
                f"Noise filter: {point.get('label')} is smaller than {0.6:.1f} ATR"
                if point.get("filtered")
                else f"Swing {point.get('label')} at {float(point['price']):.4f}"
            ),
        })

    last_break: dict | None = None
    pullback_on = False
    band = max(float(atr), 1e-9)
    for index in range(1, len(candles)):
        confirmed = [
            point for point in labeled
            if not point.get("filtered") and int(point["index"]) + right < index
        ]
        highs = [point for point in confirmed if point["kind"] == "high"]
        lows = [point for point in confirmed if point["kind"] == "low"]
        candle = candles[index]
        previous = candles[index - 1]
        close = float(candle["close"])
        prev_close = float(previous["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        bar_range = high - low
        if bar_range >= 2.2 * band:
            direction = "upper" if close >= float(candle["open"]) else "lower"
            events.append(_event(
                "spike", direction, candle, close,
                f"Range {bar_range:.4f} is at least 2.2 ATR",
            ))
        if not highs or not lows:
            continue
        swing_high = highs[-1]
        swing_low = lows[-1]
        up = (
            len(highs) >= 2
            and len(lows) >= 2
            and highs[-1]["price"] > highs[-2]["price"]
            and lows[-1]["price"] > lows[-2]["price"]
        )
        down = (
            len(highs) >= 2
            and len(lows) >= 2
            and highs[-1]["price"] < highs[-2]["price"]
            and lows[-1]["price"] < lows[-2]["price"]
        )
        level_high = float(swing_high["price"])
        level_low = float(swing_low["price"])

        if up and prev_close <= level_high < close:
            events.append(_event(
                "bos", "upper", candle, level_high,
                "Bullish break of structure through the last higher high",
            ))
            events.append(_event(
                "trend_continuation", "upper", candle, level_high,
                "Uptrend continuation after a break of structure",
            ))
            last_break = {"index": index, "direction": "upper", "level": level_high}
        elif down and prev_close >= level_low > close:
            events.append(_event(
                "bos", "lower", candle, level_low,
                "Bearish break of structure through the last lower low",
            ))
            events.append(_event(
                "trend_continuation", "lower", candle, level_low,
                "Downtrend continuation after a break of structure",
            ))
            last_break = {"index": index, "direction": "lower", "level": level_low}

        if up and prev_close >= level_low > close:
            events.append(_event(
                "choch", "lower", candle, level_low,
                "Bearish change of character: uptrend broke the last higher low",
            ))
            events.append(_event(
                "reversal", "lower", candle, level_low,
                "Structure reversal against the prior uptrend",
            ))
        elif down and prev_close <= level_high < close:
            events.append(_event(
                "choch", "upper", candle, level_high,
                "Bullish change of character: downtrend broke the last lower high",
            ))
            events.append(_event(
                "reversal", "upper", candle, level_high,
                "Structure reversal against the prior downtrend",
            ))

        if high > level_high and close < level_high:
            events.append(_event(
                "liquidity_sweep", "lower", candle, level_high,
                "Wick took the prior high and the close returned inside",
            ))
        if low < level_low and close > level_low:
            events.append(_event(
                "liquidity_sweep", "upper", candle, level_low,
                "Wick took the prior low and the close returned inside",
            ))

        if last_break and 0 < index - int(last_break["index"]) <= 3:
            failed_up = last_break["direction"] == "upper" and close < float(last_break["level"])
            failed_down = last_break["direction"] == "lower" and close > float(last_break["level"])
            if failed_up or failed_down:
                events.append(_event(
                    "false_breakout",
                    "lower" if failed_up else "upper",
                    candle,
                    float(last_break["level"]),
                    "Close broke the level and returned back through it",
                ))
                last_break = None

        pullback = False
        if up and close > float(candle["open"]) and low > level_low:
            recent_high = max(float(item["high"]) for item in candles[max(0, index - 6):index])
            pullback = recent_high - low >= 0.8 * band and close < recent_high
            if pullback and not pullback_on:
                events.append(_event(
                    "pullback", "upper", candle, low,
                    "Pullback in an uptrend held above the last higher low",
                ))
        elif down and close < float(candle["open"]) and high < level_high:
            recent_low = min(float(item["low"]) for item in candles[max(0, index - 6):index])
            pullback = high - recent_low >= 0.8 * band and close > recent_low
            if pullback and not pullback_on:
                events.append(_event(
                    "pullback", "lower", candle, high,
                    "Pullback in a downtrend held below the last lower high",
                ))
        pullback_on = pullback

    if len(candles) >= 15:
        window = candles[-15:]
        window_high = max(float(item["high"]) for item in window)
        window_low = min(float(item["low"]) for item in window)
        if window_high - window_low <= 3 * band:
            events.append(_event(
                "sideways_market", "neutral", candles[-1], window_high,
                "Recent range is tight enough to treat the market as sideways",
                low=window_low, high=window_high,
            ))

    deduped = {}
    for event in events:
        deduped[event["id"]] = event
    return {
        "events": list(deduped.values()),
        "filtered_swings": sum(1 for point in labeled if point.get("filtered")),
        "swing_labels": [point.get("label") for point in labeled if not point.get("filtered")],
    }


def volatility_state(candles: list[dict], atr: float) -> dict:
    if len(candles) < 5:
        return {"state": "unknown", "atr": float(atr), "ratio": None}
    ranges = sorted(float(item["high"]) - float(item["low"]) for item in candles[-20:])
    typical = ranges[len(ranges) // 2] or 1e-9
    ratio = float(atr) / typical
    if ratio >= 1.35:
        state = "high"
    elif ratio <= 0.75:
        state = "low"
    else:
        state = "normal"
    return {"state": state, "atr": round(float(atr), 5), "ratio": round(ratio, 3)}


def trend_reading(indicators: dict, atr: float) -> dict:
    ema20 = indicators.get("ema20")
    ema50 = indicators.get("ema50")
    adx = indicators.get("adx")
    close = indicators.get("close")
    if ema20 is None or ema50 is None:
        label = "Sideways"
        direction = "neutral"
    else:
        gap = abs(float(ema20) - float(ema50))
        flat = gap < max(float(atr), 1e-9) * 0.15
        if flat:
            label, direction = "Sideways", "neutral"
        elif float(ema20) > float(ema50):
            label, direction = "Bullish", "upper"
        else:
            label, direction = "Bearish", "lower"
    if adx is None:
        strength = "unknown"
    elif float(adx) >= 25:
        strength = "strong"
    elif float(adx) >= 18:
        strength = "moderate"
    else:
        strength = "weak"
    return {
        "label": label,
        "direction": direction,
        "strength": strength,
        "adx": None if adx is None else round(float(adx), 2),
        "ema20": ema20,
        "ema50": ema50,
        "close": close,
    }
