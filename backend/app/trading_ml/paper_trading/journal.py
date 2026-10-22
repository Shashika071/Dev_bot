"""Paper-trading journal. Backtest rows are never written here."""
from __future__ import annotations

import json
from pathlib import Path

from app.trading_ml.backtesting.contracts import break_even, settle_contract
from app.trading_ml.evaluation.metrics import max_drawdown, wilson_interval
from app.trading_ml.risk.limits import colombo_day, next_stake


def read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_journal(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def summarize(rows: list[dict], *, now: int) -> dict:
    trades = [row for row in rows if row.get("kind") == "trade"]
    resolved = [row for row in trades if row.get("outcome") in {"win", "loss"}]
    profits = [float(row["profit_per_stake"]) for row in resolved if row.get("profit_per_stake") is not None]
    wins = sum(1 for row in resolved if row["outcome"] == "win")
    consecutive = 0
    for row in reversed(resolved):
        if row["outcome"] != "loss":
            break
        consecutive += 1
    lower, upper = wilson_interval(wins, len(resolved))
    payouts = [float(row["payout"]) for row in resolved if isinstance(row.get("payout"), (int, float))]
    breakeven = sum(break_even(payout) for payout in payouts) / len(payouts) if payouts else None
    day = colombo_day(now)
    return {
        "source": "paper",
        "recorded": len(trades),
        "rejections": sum(1 for row in rows if row.get("kind") == "rejection"),
        "resolved": len(resolved),
        "open": sum(1 for row in trades if row.get("outcome") in {None, "open"}),
        "wins": wins,
        "losses": sum(1 for row in resolved if row["outcome"] == "loss"),
        "profit": None if not profits else float(sum(profits)),
        "max_drawdown": max_drawdown(profits),
        "consecutive_losses": consecutive,
        "daily_signals": sum(1 for row in trades if row.get("day") == day),
        "wilson_lower": lower,
        "wilson_upper": upper,
        "mean_break_even": breakeven,
        "recent": rows[-8:],
        "stake_policy": "fixed_unit",
        "next_stake": next_stake(profits[-1] if profits else None),
    }


def resolve_outcomes(rows: list[dict], candles: list[dict], *, now: int) -> list[dict]:
    by_epoch = {int(row["epoch"]): row for row in candles}
    updated = []
    for row in rows:
        item = dict(row)
        if item.get("kind") == "trade" and item.get("outcome") in {None, "open"} and now >= int(item["expiry_epoch"]) + 60:
            entry = by_epoch.get(int(item["entry_epoch"]))
            exit_epoch = int(item["entry_epoch"]) + int(item["duration_seconds"]) - 60
            exit_row = by_epoch.get(exit_epoch)
            if entry is None or exit_row is None:
                item["outcome"] = "unresolved"
                item["resolution_reason"] = "Settlement candles are missing, so the outcome was not invented."
            else:
                settled = settle_contract(item["direction"], entry["open"], exit_row["close"], item.get("payout"))
                item.update(outcome=settled["outcome"], profit_per_stake=settled["profit_per_stake"],
                            exit_price=float(exit_row["close"]), entry_price=float(entry["open"]))
        updated.append(item)
    return updated
