"""Resolve Volatility 100 Index and require a 5-tick Digit Matches contract."""

from __future__ import annotations

from app.digitmatch import (
    CONTRACT_TYPE,
    DURATION_TICKS,
    EXPECTED_DISPLAY_NAME,
    EXPECTED_LEGACY_SYMBOL,
)


class MarketUnavailable(Exception):
    pass


def resolve_volatility_100(active_symbols: list[dict]) -> dict:
    matches = []
    for row in active_symbols:
        name = str(row.get("display_name") or "").strip()
        if name.lower() == EXPECTED_DISPLAY_NAME.lower():
            matches.append(row)
    if len(matches) != 1:
        raise MarketUnavailable(
            "Volatility 100 Index was not uniquely available. "
            "No other instrument will be substituted."
        )
    row = matches[0]
    symbol = str(row.get("symbol") or "")
    if not symbol or symbol.upper().startswith("1HZ") or "1s" in symbol.lower():
        raise MarketUnavailable("Refusing Volatility 100 (1s) or an unverified symbol")
    return {
        "symbol": symbol,
        "display_name": str(row.get("display_name")),
        "expected_legacy_symbol": EXPECTED_LEGACY_SYMBOL,
        "matches_legacy_symbol": symbol == EXPECTED_LEGACY_SYMBOL,
        "pip": row.get("pip"),
    }


def _tick_bound(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.endswith("t"):
        text = text[:-1]
    try:
        return int(text)
    except ValueError:
        return None


def validate_digitmatch_five_ticks(contracts_for: dict) -> dict:
    available = contracts_for.get("available") or []
    chosen = None
    for row in available:
        if row.get("contract_type") != CONTRACT_TYPE:
            continue
        expiry = str(row.get("expiry_type") or "").lower()
        if expiry and expiry not in {"tick", "ticks"}:
            continue
        minimum = _tick_bound(row.get("min_contract_duration"))
        maximum = _tick_bound(row.get("max_contract_duration"))
        if minimum is None or maximum is None:
            continue
        if minimum <= DURATION_TICKS <= maximum:
            chosen = row
            break
    if chosen is None:
        raise MarketUnavailable(
            "DIGITMATCH for exactly 5 ticks is not available. "
            "No other contract or duration will be substituted."
        )
    return {
        "contract_type": CONTRACT_TYPE,
        "duration": DURATION_TICKS,
        "duration_unit": "t",
        "min_stake": chosen.get("min_stake"),
        "display": chosen.get("contract_display"),
    }
