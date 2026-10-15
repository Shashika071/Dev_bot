"""Load ticks and optional quote files. Nothing is written back to the source."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_ticks(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Tick file not found: {source}")
    suffix = source.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(source)
    elif suffix == ".parquet":
        frame = pd.read_parquet(source)
    else:
        raise ValueError("Tick file must be .csv or .parquet")
    return frame


def load_quotes(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if source.suffix.lower() == ".parquet":
        frame = pd.read_parquet(source)
    else:
        frame = pd.read_csv(source)
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    required = {"epoch", "ask_price", "payout", "barrier_direction"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Quote file is missing columns: {sorted(missing)}")
    return frame


def load_postgres(dsn: str, symbol: str) -> pd.DataFrame:
    import psycopg2

    query = """
        SELECT symbol, epoch, quote
        FROM ticks
        WHERE symbol = %s
        ORDER BY epoch
    """
    with psycopg2.connect(dsn) as conn:
        return pd.read_sql_query(query, conn, params=(symbol,))


def matched_net_profit_r(
    quotes: pd.DataFrame | None,
    decision_epochs: list[int] | pd.Series,
    *,
    target: str,
    horizon_seconds: int,
    barrier_distance: float,
) -> dict:
    """Median stake-basis r from quotes at or before development decisions only."""
    if quotes is None or len(quotes) == 0:
        return {"source": "unavailable", "r": None, "matches": 0}
    if target not in ("upper_touch", "lower_touch"):
        return {
            "source": "unavailable",
            "r": None,
            "matches": 0,
            "note": "Direction labels do not map to a one-touch quote.",
        }
    direction = "upper" if target == "upper_touch" else "lower"
    frame = quotes.copy()
    frame["epoch"] = pd.to_numeric(frame["epoch"], errors="coerce")
    frame["ask_price"] = pd.to_numeric(frame["ask_price"], errors="coerce")
    frame["payout"] = pd.to_numeric(frame["payout"], errors="coerce")
    frame = frame.dropna(subset=["epoch", "ask_price", "payout", "barrier_direction"])
    frame = frame.loc[frame["barrier_direction"].astype(str).str.lower() == direction]
    if "duration_seconds" in frame.columns:
        frame = frame.loc[pd.to_numeric(frame["duration_seconds"], errors="coerce") == int(horizon_seconds)]
    if "barrier_distance" in frame.columns:
        distance = pd.to_numeric(frame["barrier_distance"], errors="coerce")
        frame = frame.loc[(distance - float(barrier_distance)).abs() <= 1e-6]
    frame = frame.loc[(frame["ask_price"] > 0) & (frame["payout"] > frame["ask_price"])]
    frame = frame.sort_values("epoch")
    if frame.empty:
        return {"source": "unavailable", "r": None, "matches": 0, "note": "No quotes matched this target, horizon, and barrier."}
    values = []
    epochs = frame["epoch"].to_numpy(dtype=float)
    rs = (frame["payout"].to_numpy(dtype=float) - frame["ask_price"].to_numpy(dtype=float)) / frame["ask_price"].to_numpy(dtype=float)
    for decision in decision_epochs:
        eligible = epochs <= float(decision)
        if not eligible.any():
            continue
        idx = int(eligible.sum() - 1)
        if float(decision) - epochs[idx] > 120:
            continue
        values.append(float(rs[idx]))
    if len(values) < 5:
        return {
            "source": "unavailable",
            "r": None,
            "matches": int(len(values)),
            "note": "Fewer than 5 development-period quote matches.",
        }
    return {
        "source": "historical_quotes",
        "r": float(np_median(values)),
        "matches": int(len(values)),
        "note": (
            "r = (payout - ask) / ask on quotes matched to development decisions only. "
            "This is not a fill model and it does not include a test-period quote."
        ),
    }


def np_median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])
