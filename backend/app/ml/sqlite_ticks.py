"""Load and stage ticks from candel/market.sqlite for training."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


def default_sqlite_path() -> Path | None:
    """Prefer MARKET_SQLITE_PATH, then mounted data/, then repo candel/."""
    env = os.environ.get("MARKET_SQLITE_PATH", "").strip()
    if env:
        path = Path(env)
        return path if path.is_file() else None
    here = Path(__file__).resolve()
    # backend/app/ml/sqlite_ticks.py → repo root
    repo = here.parents[3]
    candidates = [
        Path("/app/data/market.sqlite"),
        Path("/app/data/candel/market.sqlite"),
        repo / "data" / "market.sqlite",
        repo / "data" / "candel" / "market.sqlite",
        repo / "candel" / "market.sqlite",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_sqlite_ticks(path: str | Path, symbol: str = "R_100") -> pd.DataFrame:
    """Read ticks from the market.sqlite schema: symbol, epoch, price."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"SQLite file not found: {source}")
    query = """
        SELECT symbol, epoch, price AS quote
        FROM ticks
        WHERE symbol = ?
        ORDER BY epoch
    """
    with sqlite3.connect(str(source)) as conn:
        frame = pd.read_sql_query(query, conn, params=(str(symbol),))
    if frame.empty:
        # Some files use only epoch/price without a usable symbol filter
        with sqlite3.connect(str(source)) as conn:
            frame = pd.read_sql_query(
                "SELECT COALESCE(symbol, ?) AS symbol, epoch, price AS quote FROM ticks ORDER BY epoch",
                conn,
                params=(str(symbol),),
            )
            if "symbol" in frame.columns:
                frame = frame.loc[frame["symbol"].astype(str) == str(symbol)].copy()
    if frame.empty:
        raise ValueError(f"No ticks for {symbol} in {source}")
    frame["epoch"] = pd.to_numeric(frame["epoch"], errors="coerce")
    frame["quote"] = pd.to_numeric(frame["quote"], errors="coerce")
    frame = frame.dropna(subset=["epoch", "quote"])
    frame = frame.drop_duplicates("epoch", keep="first")
    frame = frame.sort_values("epoch").reset_index(drop=True)
    frame["epoch"] = frame["epoch"].astype("int64")
    frame["quote"] = frame["quote"].astype("float64")
    frame["symbol"] = str(symbol)
    frame["tick_time"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
    logger.info(
        "sqlite_ticks_loaded",
        path=str(source),
        symbol=symbol,
        n=len(frame),
        start=int(frame["epoch"].iloc[0]),
        end=int(frame["epoch"].iloc[-1]),
    )
    return frame.loc[:, ["symbol", "epoch", "quote", "tick_time"]]


def count_sqlite_ticks(path: str | Path, symbol: str = "R_100") -> int:
    source = Path(path)
    with sqlite3.connect(str(source)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM ticks WHERE symbol = ?",
            (str(symbol),),
        ).fetchone()
    return int(row[0] if row else 0)


def export_sqlite_to_parquet(
    sqlite_path: str | Path,
    parquet_dir: str | Path,
    symbol: str = "R_100",
    frame: pd.DataFrame | None = None,
) -> Path:
    """Write one parquet file the live trainer can pick up via prefer_longer_history."""
    if frame is None:
        frame = load_sqlite_ticks(sqlite_path, symbol=symbol)
    out_dir = Path(parquet_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = int(frame["epoch"].iloc[0])
    end = int(frame["epoch"].iloc[-1])
    dest = out_dir / f"{symbol}_sqlite_{start}_{end}.parquet"
    frame.loc[:, ["symbol", "epoch", "quote"]].to_parquet(dest, index=False)
    logger.info("sqlite_exported_parquet", path=str(dest), n=len(frame))
    return dest
