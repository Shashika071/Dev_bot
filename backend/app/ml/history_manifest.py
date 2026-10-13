"""Load retained Parquet history when it is longer than the database window."""

from __future__ import annotations

import glob
import os

import pandas as pd

from app.config import settings


def read_parquet_ticks(symbol: str) -> pd.DataFrame | None:
    root = settings.parquet_data_dir
    if not root or not os.path.isdir(root):
        return None
    frames = []
    for path in glob.glob(os.path.join(root, "**", "*.parquet"), recursive=True):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if "symbol" in frame.columns:
            frame = frame[frame["symbol"].astype(str) == str(symbol)]
        if "quote" not in frame.columns and "price" in frame.columns:
            frame = frame.rename(columns={"price": "quote"})
        if not {"epoch", "quote"}.issubset(frame.columns) or frame.empty:
            continue
        frames.append(frame[["epoch", "quote"]].copy())
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    out["epoch"] = pd.to_numeric(out["epoch"], errors="coerce")
    out["quote"] = pd.to_numeric(out["quote"], errors="coerce")
    out = out.dropna(subset=["epoch", "quote"]).drop_duplicates("epoch")
    out = out.sort_values("epoch")
    out["epoch"] = out["epoch"].astype("int64")
    out["tick_time"] = pd.to_datetime(out["epoch"], unit="s", utc=True)
    return out.reset_index(drop=True)


def prefer_longer_history(database_frame: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, str]:
    parquet = read_parquet_ticks(symbol)
    if parquet is not None and len(parquet) > len(database_frame):
        return parquet, "parquet"
    return database_frame, "database"
