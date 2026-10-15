"""Normalize tick tables and describe data quality without inventing rows."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

LABEL_LIKE = {
    "label",
    "labels",
    "y",
    "target",
    "touched",
    "upper",
    "lower",
    "direction",
    "y_upper",
    "y_lower",
    "y_direction",
}


def prepare_ticks(raw: pd.DataFrame, symbol: str | None) -> tuple[pd.DataFrame | None, dict]:
    """Return a sorted single-instrument frame and a quality report.

    The input frame is not modified. True duplicate (symbol, epoch, quote)
    extras are removed. Repeated prices at different epochs are kept.
    """
    report: dict = {
        "raw_columns": {str(col): str(raw[col].dtype) for col in raw.columns},
        "raw_row_count": int(len(raw)),
        "existing_label_columns": [str(col) for col in raw.columns if str(col).lower() in LABEL_LIKE],
        "existing_labels_used_for_training": False,
    }
    if report["existing_label_columns"]:
        report["existing_label_note"] = (
            "The file already has label-like columns. Training ignores them and "
            "builds labels from tick prices with the configured entry, expiry, and barrier rules."
        )
    frame = _standardize(raw, report)
    if frame is None:
        report["status"] = "unusable"
        return None, report

    order_breaks = int((frame["epoch"].diff().dropna() < 0).sum()) if len(frame) else 0
    report["out_of_order_pairs_before_sort"] = order_breaks
    frame = frame.sort_values(["symbol", "epoch"], kind="mergesort").reset_index(drop=True)

    extras = frame.duplicated(["symbol", "epoch"], keep="first")
    true_dups = 0
    conflicts = 0
    if extras.any():
        kept = frame.drop_duplicates(["symbol", "epoch"], keep="first")
        merged = frame.loc[extras, ["symbol", "epoch", "quote"]].merge(
            kept[["symbol", "epoch", "quote"]],
            on=["symbol", "epoch"],
            suffixes=("_extra", "_kept"),
        )
        true_dups = int((merged["quote_extra"] == merged["quote_kept"]).sum())
        conflicts = int((merged["quote_extra"] != merged["quote_kept"]).sum())
    report["true_duplicate_rows_removed"] = true_dups
    report["conflicting_same_epoch_rows_removed"] = conflicts
    frame = frame.drop_duplicates(["symbol", "epoch"], keep="first").reset_index(drop=True)

    invalid = ~np.isfinite(frame["quote"].to_numpy(dtype=float)) | (frame["quote"].to_numpy(dtype=float) <= 0)
    report["invalid_price_rows_removed"] = int(invalid.sum())
    frame = frame.loc[~invalid].reset_index(drop=True)

    counts = frame.groupby("symbol").size().astype(int).to_dict()
    report["instruments"] = {str(k): int(v) for k, v in counts.items()}
    if symbol:
        if symbol not in set(frame["symbol"].astype(str)):
            report["status"] = "symbol_not_found"
            report["requested_symbol"] = symbol
            return None, report
        frame = frame.loc[frame["symbol"].astype(str) == str(symbol)].reset_index(drop=True)
    elif frame["symbol"].nunique() > 1:
        report["status"] = "multiple_instruments"
        report["note"] = "Pass --symbol. Instruments are not mixed in one training set."
        return None, report

    report["instrument"] = str(frame["symbol"].iloc[0]) if len(frame) else None
    report["symbol_was_in_file"] = bool(report.get("symbol_was_in_file", False))
    report["tick_count"] = int(len(frame))
    report["normalized_columns"] = {str(col): str(frame[col].dtype) for col in frame.columns}
    if len(frame) == 0:
        report["status"] = "no_ticks"
        return None, report

    epochs = frame["epoch"].to_numpy(dtype=np.int64)
    deltas = np.diff(epochs)
    median_dt = float(np.median(deltas)) if len(deltas) else None
    report["timestamp_unit_after_normalization"] = "seconds"
    report["timezone"] = "UTC"
    report["median_tick_interval_seconds"] = median_dt
    if median_dt is not None and len(deltas):
        hole = deltas > max(3.0 * median_dt, 1.0)
        report["missing_interval_count"] = int(hole.sum())
        report["longest_gap_seconds"] = int(deltas.max())
        report["gaps_over_5_seconds"] = int((deltas > 5).sum())
        report["gaps_over_30_seconds"] = int((deltas > 30).sum())
    else:
        report["missing_interval_count"] = 0
        report["longest_gap_seconds"] = 0
    start = datetime.fromtimestamp(int(epochs[0]), tz=timezone.utc)
    end = datetime.fromtimestamp(int(epochs[-1]), tz=timezone.utc)
    report["start_utc"] = start.isoformat()
    report["end_utc"] = end.isoformat()
    report["span_hours"] = (int(epochs[-1]) - int(epochs[0])) / 3600.0
    report["status"] = "ok"
    report["note"] = (
        "Span hours are the difference between the first and last stored tick. "
        "They are not a trading-session calendar and they are not a label count."
    )
    return frame, report


def _standardize(raw: pd.DataFrame, report: dict) -> pd.DataFrame | None:
    frame = raw.copy()
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    rename = {}
    if "quote" not in frame.columns:
        for candidate in ("price", "spot", "mid"):
            if candidate in frame.columns:
                rename[candidate] = "quote"
                break
    if "epoch" not in frame.columns:
        for candidate in ("timestamp", "time", "ts", "epoch_ms"):
            if candidate in frame.columns:
                rename[candidate] = "epoch"
                break
    frame = frame.rename(columns=rename)
    if "quote" not in frame.columns or "epoch" not in frame.columns:
        report["status"] = "missing_columns"
        report["required_columns"] = ["epoch (or timestamp)", "quote (or price)"]
        report["optional_columns"] = ["symbol"]
        return None

    if "symbol" not in frame.columns:
        frame["symbol"] = "UNSPECIFIED"
        report["symbol_was_in_file"] = False
        report["symbol_note"] = (
            "No symbol column was present. Rows are marked UNSPECIFIED and are not "
            "assumed to be a particular Volatility contract."
        )
    else:
        report["symbol_was_in_file"] = True
        frame["symbol"] = frame["symbol"].astype(str)

    epoch_series = frame["epoch"]
    if np.issubdtype(epoch_series.dtype, np.datetime64) or str(epoch_series.dtype).startswith("datetime"):
        parsed = pd.to_datetime(epoch_series, utc=True)
        report["timestamp_input"] = "datetime"
        report["timezone_assumption"] = "converted to UTC"
        frame["epoch"] = (parsed.astype("int64") // 1_000_000_000).astype("int64")
    else:
        numeric = pd.to_numeric(epoch_series, errors="coerce")
        if numeric.notna().sum() == 0:
            preview = pd.to_datetime(epoch_series, errors="coerce")
            if preview.notna().sum() == 0:
                report["status"] = "unparseable_timestamps"
                return None
            naive = getattr(preview.dt, "tz", None) is None
            parsed = pd.to_datetime(epoch_series, utc=True, errors="coerce")
            report["timestamp_input"] = "datetime_string"
            report["naive_timestamps_assumed_utc"] = bool(naive)
            report["timezone"] = "UTC"
            frame["epoch"] = (parsed.astype("int64") // 1_000_000_000).astype("int64")
        else:
            unit, divisor = _epoch_unit(numeric.dropna().to_numpy(dtype=float))
            report["timestamp_input"] = unit
            report["timezone"] = "UTC epoch"
            frame["epoch"] = (numeric / divisor).round().astype("Int64")

    frame["quote"] = pd.to_numeric(frame["quote"], errors="coerce")
    before = len(frame)
    frame = frame.dropna(subset=["epoch", "quote"]).copy()
    report["rows_dropped_unparseable"] = int(before - len(frame))
    frame["epoch"] = frame["epoch"].astype("int64")
    frame["quote"] = frame["quote"].astype("float64")
    return frame.loc[:, ["symbol", "epoch", "quote"]]


def _epoch_unit(values: np.ndarray) -> tuple[str, float]:
    med = float(np.median(values))
    if med > 1e14:
        return "microseconds", 1_000_000.0
    if med > 1e11:
        return "milliseconds", 1_000.0
    return "seconds", 1.0
