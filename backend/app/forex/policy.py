"""Fixed product/risk policy, independent of the legacy Touch preferences."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
DEFAULT_SYMBOLS = ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD", "frxUSDCAD", "frxUSDCHF"]
TIMEZONE = "Asia/Colombo"
DAILY_CAP = 2
# Product lock: analysis, cooldown, and demo buys are evaluated for four hours only.
DURATION_SECONDS = 14400


def default_config() -> dict:
    return {"duration_seconds": DURATION_SECONDS, "max_signals_per_day": DAILY_CAP,
            "cooldown_seconds": DURATION_SECONDS, "stake": 1.0, "timezone": TIMEZONE,
            "symbols": list(DEFAULT_SYMBOLS), "demo_auto_trade": True}


def validate_config(updates: dict, previous: dict | None = None) -> dict:
    config = {**default_config(), **(previous or {}), **updates}
    requested = updates.get("duration_seconds", DURATION_SECONDS)
    if requested not in (None, DURATION_SECONDS):
        raise ValueError("Forex Rise/Fall expiry is fixed at four hours")
    stake = float(config["stake"])
    if not math.isfinite(stake) or not 0.35 <= stake <= 10:
        raise ValueError("Demo stake must be between 0.35 and 10 account currency units")
    symbols = config["symbols"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 12:
        raise ValueError("Choose between 1 and 12 Forex pairs")
    if any(not isinstance(s, str) or not re.fullmatch(r"frx[A-Z]{6}", s) for s in symbols):
        raise ValueError("Only Forex currency pairs are supported")
    config.update(symbols=list(dict.fromkeys(symbols)), stake=round(stake, 2),
                  duration_seconds=DURATION_SECONDS, max_signals_per_day=DAILY_CAP,
                  timezone=TIMEZONE, cooldown_seconds=DURATION_SECONDS)
    return config


def local_day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(ZoneInfo(TIMEZONE)).date().isoformat()


def duration_value(value: object) -> int | None:
    match = re.fullmatch(r"(\d+)([smhd])", str(value or ""))
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]] if match else None


def supports_rise_fall(contracts: list[dict], duration: int) -> bool:
    """Require explicit zero barriers; CALL/PUT also identify Higher/Lower contracts."""
    supported = set()
    for c in contracts:
        if str(c.get("barriers")) != "0" or c.get("contract_type") not in {"CALL", "PUT"}:
            continue
        if c.get("start_type", "spot") != "spot":
            continue
        lower = duration_value(c.get("min_contract_duration"))
        upper = duration_value(c.get("max_contract_duration"))
        if lower is not None and duration < lower or upper is not None and duration > upper:
            continue
        supported.add(c["contract_type"])
    # Missing duration metadata is verified by an authenticated proposal before buy.
    return supported == {"CALL", "PUT"}


def session_context(now: int, duration_seconds: int) -> dict:
    dt = datetime.fromtimestamp(now, timezone.utc)
    active = []
    factors = []
    for number, name, zone, start, end in [
        (30, "Asian session", "Asia/Tokyo", 9, 18),
        (31, "London session", "Europe/London", 8, 17),
        (32, "New York session", "America/New_York", 8, 17),
    ]:
        local = dt.astimezone(ZoneInfo(zone))
        opened = local.weekday() < 5 and start <= local.hour < end
        if opened:
            active.append(name)
        factors.append({"id": number, "name": name, "status": "active" if opened else "inactive",
                        "detail": f"{local:%H:%M %Z}; indicative {start:02}:00–{end:02}:00 local session (DST aware)."})
    overlap = "London session" in active and "New York session" in active
    factors.append({"id": 33, "name": "Session overlap", "status": "active" if overlap else "inactive",
                    "detail": "London/New York overlap" if overlap else "Outside London/New York overlap"})
    blockers = []
    # Conservative conventional FX weekend guard. Broker availability and the actual
    # contract proposal remain authoritative, including holidays and instrument breaks.
    for offset in range(0, duration_seconds + 1, 60):
        ny = (dt + timedelta(seconds=offset)).astimezone(ZoneInfo("America/New_York"))
        if ny.weekday() == 5 or ny.weekday() == 6 and ny.hour < 17 or ny.weekday() == 4 and ny.hour >= 17:
            blockers.append("Trade window overlaps the conventional Forex weekend close")
            break
    factors.append({"id": 34, "name": "Day of week", "status": "blocked" if blockers else "context",
                    "detail": f"{dt:%A}; complete holding window checked against the FX weekend."})
    factors.extend([
        {"id": 35, "name": "Market liquidity", "status": "proxy", "detail": "Session activity and fresh broker prices are proxies; order-book depth is unavailable."},
        {"id": 36, "name": "Spread", "status": "unavailable", "detail": "Options OHLC is not a bid/ask feed. Contract ask/payout and breakeven replace a fabricated CFD spread."},
        {"id": 37, "name": "Slippage", "status": "context", "detail": "A fresh quote and maximum buy price bound premium cost; manual/actual entry can differ from the analysis spot."},
    ])
    return {"factors": factors, "blockers": blockers, "active": active}
