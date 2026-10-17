"""Load retained Parquet history when it is longer than the database window."""

from __future__ import annotations

import glob
import os

import pandas as pd

from app.config import settings


def _parquet_paths_for_symbol(symbol: str) -> list[str]:
    root = settings.parquet_data_dir
    if not root or not os.path.isdir(root):
        return []
    paths = []
    for path in glob.glob(os.path.join(root, "**", "*.parquet"), recursive=True):
        name = os.path.basename(path)
        # Prefer filenames that mention the symbol; otherwise keep as fallback.
        if str(symbol) in name or name.startswith(str(symbol)):
            paths.append(path)
    if paths:
        return paths
    return glob.glob(os.path.join(root, "**", "*.parquet"), recursive=True)


def count_parquet_ticks(symbol: str) -> int:
    """Fast row count from parquet metadata (does not load all ticks into RAM)."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return 0
    total = 0
    for path in _parquet_paths_for_symbol(symbol):
        try:
            total += int(pq.ParquetFile(path).metadata.num_rows)
        except Exception:
            continue
    return total


def parquet_epoch_span(symbol: str) -> dict | None:
    """
    Fast min/max epoch + row count from parquet column statistics.
    Avoids loading millions of ticks into RAM for /train/data-info.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return None
    total = 0
    e_min: int | None = None
    e_max: int | None = None
    for path in _parquet_paths_for_symbol(symbol):
        try:
            pf = pq.ParquetFile(path)
            meta = pf.metadata
            total += int(meta.num_rows)
            # Find epoch column index
            schema = pf.schema_arrow
            try:
                col_idx = schema.get_field_index("epoch")
            except (KeyError, ValueError):
                names = [schema.field(i).name for i in range(len(schema))]
                if "epoch" not in names:
                    continue
                col_idx = names.index("epoch")
            for rg in range(meta.num_row_groups):
                col = meta.row_group(rg).column(col_idx)
                stats = col.statistics
                if stats is None or not stats.has_min_max:
                    continue
                lo = int(stats.min)
                hi = int(stats.max)
                e_min = lo if e_min is None else min(e_min, lo)
                e_max = hi if e_max is None else max(e_max, hi)
        except Exception:
            continue
    if total <= 0 or e_min is None or e_max is None:
        return None
    return {
        "tick_count": total,
        "oldest_epoch": int(e_min),
        "newest_epoch": int(e_max),
        "span_seconds": max(0, int(e_max) - int(e_min)),
    }


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
    """Prefer the longest history without reloading SQLite when parquet already exists."""
    candidates: list[tuple[pd.DataFrame, str]] = [(database_frame, "database")]
    parquet = read_parquet_ticks(symbol)
    if parquet is not None and not parquet.empty:
        candidates.append((parquet, "parquet"))
    else:
        # SQLite load is expensive (~millions of rows). Use it only when parquet is missing.
        try:
            from app.ml.sqlite_ticks import default_sqlite_path, load_sqlite_ticks

            sqlite_path = default_sqlite_path()
            if sqlite_path is not None:
                sqlite_frame = load_sqlite_ticks(sqlite_path, symbol=symbol)
                if not sqlite_frame.empty:
                    candidates.append((sqlite_frame.loc[:, ["epoch", "quote", "tick_time"]], "sqlite"))
        except Exception:
            pass
    best_frame, best_source = max(candidates, key=lambda item: len(item[0]))
    return best_frame, best_source
