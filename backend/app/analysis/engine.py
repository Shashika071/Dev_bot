"""Assemble a chart-ready analysis snapshot from closed candles."""

from __future__ import annotations

import time

from app.analysis.breakouts import breakout_events
from app.analysis.candle_patterns import detect_recent
from app.analysis.chart_patterns import chart_patterns
from app.analysis.indicators import indicator_snapshot, last_atr
from app.analysis.market_events import price_action_events, trend_reading, volatility_state
from app.analysis.strategies import evaluate_strategies, fuse
from app.analysis.structure import structure_state, support_resistance, swings, trendlines
from app.signal_engine.chart_candles import INTERVAL_SECONDS
from app.signal_engine.market_state import timeframe_strength

VOLUME_NOTE = "R_100 has no exchange volume, so volume and VWAP are not used as confirmation."
_LAST_SIGNAL: dict[str, tuple[str, float]] = {}


def closed_candles(candles: list[dict], interval: str, now: int | None = None) -> list[dict]:
    if not candles:
        return []
    step = INTERVAL_SECONDS.get(interval)
    if not step:
        return list(candles)
    current = int(now if now is not None else time.time())
    closed = [row for row in candles if int(row["epoch"]) + step <= current]
    return closed or list(candles[:-1])


def _side(score: float) -> str:
    if score >= 0.25:
        return "upper"
    if score <= -0.25:
        return "lower"
    return "neutral"


def analyze_candles(
    candles_by_interval: dict[str, list[dict]],
    *,
    symbol: str = "R_100",
    interval: str = "1m",
    now: int | None = None,
    fresh: bool = True,
    stale_reason: str | None = None,
) -> dict:
    current = int(now if now is not None else time.time())
    closed = {
        name: closed_candles(rows, name, current)
        for name, rows in candles_by_interval.items()
    }
    selected = closed.get(interval) or []
    indicators = indicator_snapshot(selected)
    points = swings(selected)
    zones = support_resistance(selected, points)
    lines = trendlines(selected, points)
    patterns = detect_recent(selected, allow_gaps=symbol.upper() not in {"R_100", "R_100_1S"})
    charts = chart_patterns(selected)
    breaks = breakout_events(selected)
    atr = last_atr(selected) if selected else 0.0
    action = price_action_events(selected, points, atr)
    events = []
    for zone in zones:
        events.append({
            "id": f"{zone['type']}:{zone['epoch']}:{round(zone['price'], 4)}",
            "type": zone["type"],
            "direction": zone["direction"],
            "epoch": zone["epoch"],
            "end_epoch": zone["end_epoch"],
            "confidence": zone["confidence"],
            "status": zone["status"],
            "geometry": {"price": zone["price"], "low": zone["low"], "high": zone["high"]},
            "evidence": zone["evidence"],
            "invalidation": zone["price"],
        })
    events.extend(lines)
    events.extend(patterns)
    events.extend(charts)
    events.extend(breaks)
    events.extend(action["events"])

    components = {}
    for name in ("1m", "5m", "15m", "30m", "1h"):
        score = timeframe_strength(closed.get(name) or [])
        components[name] = {"score": round(score, 4), "state": _side(score)}
    score_5 = components.get("5m", {}).get("score", 0)
    score_15 = components.get("15m", {}).get("score", 0)
    if score_5 >= 0.25 and score_15 >= 0.2:
        regime = "trend_up"
    elif score_5 <= -0.25 and score_15 <= -0.2:
        regime = "trend_down"
    elif abs(score_5) < 0.2 and abs(score_15) < 0.2:
        regime = "range"
    else:
        regime = "conflict"

    last_indicators = indicators.get("last") or {}
    trend = trend_reading(last_indicators, atr)
    volatility = volatility_state(selected, atr)
    if regime == "range" or trend["label"] == "Sideways":
        trend["label"] = "Sideways"
        trend["direction"] = "neutral"
    readings = evaluate_strategies(regime=regime, indicators=last_indicators, events=events)
    close = float(selected[-1]["close"]) if selected else None
    confluence = fuse(readings, fresh=fresh, atr=atr, close=close)
    checklist = _checklist(
        trend=trend,
        structure=structure_state(points),
        volatility=volatility,
        indicators=last_indicators,
        events=events,
        timeframes=components,
        fresh=fresh,
        atr=atr,
        candles=selected,
    )
    confluence["checks"] = checklist
    confluence["checks_passed"] = sum(1 for item in checklist if item["ok"])
    signal = _signal(symbol, interval, confluence, selected, current)
    buys, sells = _side_signals(events, signal)
    return {
        "schema_version": 1,
        "symbol": symbol,
        "interval": interval,
        "as_of_epoch": int(selected[-1]["epoch"]) if selected else None,
        "fresh": fresh,
        "stale_reason": stale_reason,
        "regime": regime,
        "trend": trend,
        "structure": structure_state(points),
        "volatility": volatility,
        "strength": {"adx": trend.get("adx"), "label": trend.get("strength")},
        "noise_filter": {
            "atr_multiple": 0.6,
            "filtered_swings": action["filtered_swings"],
            "detail": "Swing turns smaller than 0.6 ATR do not create BOS or CHoCH events.",
        },
        "volume_note": VOLUME_NOTE,
        "timeframes": components,
        "indicators": last_indicators,
        "series": _trim_series(indicators.get("series") or {}),
        "events": _latest_events(events),
        "strategies": readings,
        "confluence": confluence,
        "checklist": checklist,
        "signal": signal,
        "buy_signals": buys,
        "sell_signals": sells,
        "candles": _chart_candles(selected, candles_by_interval.get(interval) or []),
    }


def _chart_candles(closed: list[dict], raw: list[dict]) -> list[dict]:
    """Closed bars plus the still-forming bar so the chart can move with the latest tick."""
    rows = list(closed[-1000:])
    if not raw:
        return rows
    live = raw[-1]
    live_epoch = int(live["epoch"])
    if rows and int(rows[-1]["epoch"]) == live_epoch:
        rows[-1] = live
    elif not rows or live_epoch > int(rows[-1]["epoch"]):
        rows.append(live)
    return rows[-1000:]


def _latest_events(events: list[dict], limit: int = 120) -> list[dict]:
    """Keep the newest event of every type, then fill with the most recent rest."""
    newest: dict[str, dict] = {}
    for event in events:
        newest[str(event.get("type"))] = event
    rest = [event for event in events if event not in newest.values()]
    combined = list(newest.values()) + rest[-limit:]
    deduped = {}
    for event in combined:
        event_id = event.get("id") or f"{event.get('type')}:{event.get('epoch')}"
        deduped[event_id] = event
    return list(deduped.values())[-limit:]


def _recent(events: list[dict], name: str, candles: list[dict], lookback: int = 8) -> dict | None:
    if not candles:
        return None
    earliest = int(candles[-min(lookback, len(candles))]["epoch"])
    found = [
        event for event in events
        if event.get("type") == name and int(event.get("epoch") or 0) >= earliest
    ]
    return found[-1] if found else None


def _checklist(
    *,
    trend: dict,
    structure: str,
    volatility: dict,
    indicators: dict,
    events: list[dict],
    timeframes: dict,
    fresh: bool,
    atr: float,
    candles: list[dict],
) -> list[dict]:
    direction = trend.get("direction")
    bos = _recent(events, "bos", candles)
    choch = _recent(events, "choch", candles)
    failed = _recent(events, "false_breakout", candles, 4)
    sweep = _recent(events, "liquidity_sweep", candles, 4)
    pullback = _recent(events, "pullback", candles)
    structure_ok = (
        (direction == "upper" and structure == "HH_HL")
        or (direction == "lower" and structure == "LH_LL")
    )
    higher = [
        name for name in ("5m", "15m", "1h")
        if timeframes.get(name, {}).get("state") == direction and direction in {"upper", "lower"}
    ]
    rsi_value = indicators.get("rsi")
    hist = indicators.get("macd_hist")
    momentum_ok = False
    if direction == "upper" and rsi_value is not None and hist is not None:
        momentum_ok = float(rsi_value) >= 50 and float(hist) > 0
    if direction == "lower" and rsi_value is not None and hist is not None:
        momentum_ok = float(rsi_value) <= 50 and float(hist) < 0
    last_range = 0.0
    if candles:
        last_range = float(candles[-1]["high"]) - float(candles[-1]["low"])
    noise_ok = bool(candles) and last_range >= max(float(atr), 0) * 0.45
    sweep_against = bool(sweep and sweep.get("direction") not in {None, direction})
    return [
        {"name": "Trend", "ok": direction in {"upper", "lower"}, "detail": f"{trend.get('label')} · EMA 20/50"},
        {"name": "Market structure", "ok": structure_ok, "detail": structure},
        {"name": "Break of structure", "ok": bool(bos and bos.get("direction") == direction), "detail": (bos or {}).get("evidence") or "No recent BOS"},
        {"name": "Change of character", "ok": not (choch and choch.get("direction") != direction), "detail": (choch or {}).get("evidence") or "No opposing CHoCH"},
        {"name": "False breakout", "ok": failed is None, "detail": (failed or {}).get("evidence") or "No failed break in the last bars"},
        {"name": "Liquidity sweep", "ok": not sweep_against, "detail": (sweep or {}).get("evidence") or "No sweep against the trend"},
        {"name": "Momentum", "ok": momentum_ok, "detail": f"RSI {rsi_value} · MACD {hist}"},
        {"name": "Higher timeframe", "ok": len(higher) >= 1, "detail": ", ".join(higher) or "1m / 5m / 15m / 1h do not agree"},
        {"name": "Volatility", "ok": volatility.get("state") != "unknown" and volatility.get("atr") not in {None, 0}, "detail": f"ATR {volatility.get('atr')} · {volatility.get('state')}"},
        {"name": "Noise filter", "ok": noise_ok, "detail": "Last candle is large enough versus ATR"},
        {"name": "Pullback or continuation", "ok": bool(pullback or bos), "detail": "A held pullback or a fresh break is present"},
        {"name": "Data fresh", "ok": fresh, "detail": "Closed candles are current" if fresh else "Waiting for fresh candles"},
    ]


def _side_signals(events: list[dict], signal: dict | None) -> tuple[list[dict], list[dict]]:
    """BUY and SELL markers. One arrow per candle; the latest event on that candle wins."""
    found: dict[int, dict] = {}
    for event in events:
        direction = event.get("direction")
        if direction not in {"upper", "lower"} or event.get("status") == "filtered":
            continue
        if event.get("type") not in {"bos", "pullback", "trend_continuation", "choch", "reversal"}:
            continue
        epoch = int(event.get("end_epoch") or event["epoch"])
        found[epoch] = _marker(epoch, direction, event.get("evidence") or event.get("type"), (event.get("geometry") or {}).get("price"))
    if signal and signal.get("direction") in {"upper", "lower"}:
        epoch = int(signal["epoch"])
        zone = signal.get("entry_zone") or []
        price = zone[0] if isinstance(zone, list) and zone else None
        found[epoch] = _marker(epoch, signal["direction"], "Entry confirmation passed", price)
    ordered = [found[epoch] for epoch in sorted(found)]
    buys = [item for item in ordered if item["direction"] == "upper"][-8:]
    sells = [item for item in ordered if item["direction"] == "lower"][-8:]
    return buys, sells


def _marker(epoch: int, direction: str, reason: str, price) -> dict:
    return {
        "epoch": epoch,
        "direction": direction,
        "label": "BUY" if direction == "upper" else "SELL",
        "reason": reason,
        "price": price,
    }


def _trim_series(series: dict) -> dict:
    return {name: rows[-2000:] for name, rows in series.items()}


def _signal(symbol: str, interval: str, confluence: dict, candles: list[dict], now: int) -> dict | None:
    passed = int(confluence.get("checks_passed") or 0)
    required = {"Trend", "False breakout", "Noise filter", "Data fresh"}
    checks = {item["name"]: item["ok"] for item in confluence.get("checks") or []}
    if passed < 7 or any(not checks.get(name, False) for name in required):
        return None
    if not candles or confluence.get("direction") not in {"upper", "lower"}:
        return None
    key = f"{symbol}:{interval}:{confluence['direction']}:{confluence['strategies']}"
    previous = _LAST_SIGNAL.get(symbol)
    if previous and previous[0] == key and now - previous[1] < 300:
        confluence["cooldown"] = True
        return None
    _LAST_SIGNAL[symbol] = (key, now)
    confluence["cooldown"] = False
    return {
        "id": f"ta:{symbol}:{interval}:{candles[-1]['epoch']}:{confluence['direction']}",
        "symbol": symbol,
        "interval": interval,
        "direction": confluence["direction"],
        "epoch": int(candles[-1]["epoch"]),
        "ta_score": confluence["ta_score"],
        "evidence": confluence["evidence"],
        "invalidation": confluence["invalidation"],
        "entry_zone": confluence["entry_zone"],
        "source": "technical_analysis",
    }
