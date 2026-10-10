"""Classic Deriv WebSocket v3 message builders and parsers.

Documented family: wss://ws.derivws.com/websockets/v3?app_id=...
Auth: {"authorize": "<api token>"}
This module does not build PAT, OAuth, OTP, or underlying_symbol requests.
"""

from __future__ import annotations

from app.digitmatch import CONTRACT_TYPE, DURATION_TICKS


class DerivMessageError(Exception):
    pass


def history_request(symbol: str, *, count: int, end: str | int) -> dict:
    return {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": int(count),
        "end": end,
        "style": "ticks",
    }


def proposal_request(*, symbol: str, digit: int, stake: float, currency: str) -> dict:
    if not 0 <= int(digit) <= 9:
        raise DerivMessageError("digit must be 0..9")
    return {
        "proposal": 1,
        "amount": float(stake),
        "basis": "stake",
        "contract_type": CONTRACT_TYPE,
        "currency": currency,
        "duration": DURATION_TICKS,
        "duration_unit": "t",
        "symbol": symbol,
        "barrier": str(int(digit)),
    }


def buy_request(proposal_id: str, price: float) -> dict:
    return {"buy": proposal_id, "price": float(price)}


def authorize_request(token: str) -> dict:
    return {"authorize": token}


def parse_history_ticks(message: dict) -> tuple[list[dict], int | None]:
    pip = message.get("pip_size")
    history = message.get("history")
    rows: list[dict] = []
    if isinstance(history, dict) and "prices" in history and "times" in history:
        prices = history.get("prices") or []
        times = history.get("times") or []
        if len(prices) != len(times):
            raise DerivMessageError("history prices and times have different lengths")
        for price, epoch in zip(prices, times):
            rows.append({"quote": price, "epoch": int(epoch), "pip_size": pip, "id": None})
        return rows, int(pip) if pip is not None else None
    raise DerivMessageError("ticks_history response did not include history.prices and history.times")


def parse_tick(message: dict) -> dict:
    tick = message.get("tick")
    if not isinstance(tick, dict):
        raise DerivMessageError("tick message missing tick object")
    if "quote" not in tick or "epoch" not in tick:
        raise DerivMessageError("tick message missing quote or epoch")
    return {
        "quote": tick.get("quote"),
        "epoch": int(tick["epoch"]),
        "pip_size": tick.get("pip_size"),
        "id": None if tick.get("id") is None else str(tick.get("id")),
        "symbol": tick.get("symbol"),
    }


def parse_proposal(message: dict) -> dict:
    proposal = message.get("proposal")
    if not isinstance(proposal, dict):
        raise DerivMessageError("proposal response missing proposal object")
    for key in ("id", "ask_price", "payout"):
        if proposal.get(key) is None:
            raise DerivMessageError(f"proposal missing {key}")
    return {
        "id": str(proposal["id"]),
        "ask_price": float(proposal["ask_price"]),
        "total_payout": float(proposal["payout"]),
        "spot_time": None if proposal.get("spot_time") is None else int(proposal["spot_time"]),
        "spot": proposal.get("spot"),
        "longcode": proposal.get("longcode"),
    }


def parse_buy(message: dict) -> dict:
    buy = message.get("buy")
    if not isinstance(buy, dict) or buy.get("contract_id") is None:
        raise DerivMessageError("buy response missing contract_id")
    return {
        "contract_id": str(buy["contract_id"]),
        "buy_price": None if buy.get("buy_price") is None else float(buy["buy_price"]),
        "total_payout": None if buy.get("payout") is None else float(buy["payout"]),
        "purchase_time": buy.get("purchase_time"),
        "transaction_id": None if buy.get("transaction_id") is None else str(buy["transaction_id"]),
        "longcode": buy.get("longcode"),
        "shortcode": buy.get("shortcode"),
        "balance_after": buy.get("balance_after"),
        "start_time": buy.get("start_time"),
    }


def parse_open_contract(message: dict) -> dict:
    contract = message.get("proposal_open_contract")
    if not isinstance(contract, dict):
        raise DerivMessageError("proposal_open_contract missing")
    return {
        "contract_id": None if contract.get("contract_id") is None else str(contract.get("contract_id")),
        "status": contract.get("status"),
        "is_expired": contract.get("is_expired"),
        "is_sold": contract.get("is_sold"),
        "profit": contract.get("profit"),
        "buy_price": contract.get("buy_price"),
        "payout": contract.get("payout"),
        "entry_tick": contract.get("entry_tick"),
        "exit_tick": contract.get("exit_tick"),
        "current_spot": contract.get("current_spot"),
        "date_expiry": contract.get("date_expiry"),
        "tick_count": contract.get("tick_count") or contract.get("current_spot_time"),
        "longcode": contract.get("longcode"),
    }


def error_of(message: dict) -> tuple[str, str] | None:
    error = message.get("error")
    if not isinstance(error, dict):
        return None
    return str(error.get("code") or "unknown"), str(error.get("message") or "")
