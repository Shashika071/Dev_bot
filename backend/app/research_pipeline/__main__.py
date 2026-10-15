"""Command line for the research pipeline.

Examples (run from the backend directory):

  python -m app.research_pipeline inspect --ticks ticks.csv
  python -m app.research_pipeline train --ticks ticks.csv --out ..\\data\\research_runs\\exp
  python -m app.research_pipeline infer --model ..\\data\\research_runs\\exp\\upper_touch --ticks ticks.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

from app.research_pipeline.infer import load_bundle, score_asof, score_batch
from app.research_pipeline.io_ticks import load_postgres, load_quotes, load_ticks
from app.research_pipeline.quality import prepare_ticks
from app.research_pipeline.run import run_experiment
from app.research_pipeline.settings import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Volatility index research pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="Describe a tick file or database table")
    _add_source(inspect)

    train = sub.add_parser("train", help="Train and evaluate. Does not place orders.")
    _add_source(train)
    train.add_argument("--out", required=True)
    train.add_argument("--config", default=None)
    train.add_argument("--quotes", default=None)
    train.add_argument("--target", default=None, choices=["all", "direction", "upper_touch", "lower_touch"])
    train.add_argument("--horizon-seconds", type=int, default=None)
    train.add_argument("--barrier-distance", type=float, default=None)
    train.add_argument("--sample-every-seconds", type=int, default=None)
    train.add_argument("--hypothetical-r", type=float, default=None)
    train.add_argument("--lstm", action="store_true")
    train.add_argument("--fast", action="store_true")

    infer = sub.add_parser("infer", help="Demo score. Does not place an order.")
    _add_source(infer)
    infer.add_argument("--model", required=True, help="model.joblib or the target output directory")
    infer.add_argument("--asof", type=int, default=None)
    infer.add_argument("--batch", action="store_true")
    infer.add_argument("--out", default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "train":
            return _train(args)
        return _infer(args)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _add_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ticks", default=None, help="CSV or Parquet tick file")
    parser.add_argument("--database-url", default=None, help="Postgres URL. Defaults to DATABASE_URL if set.")
    parser.add_argument("--symbol", default=None)


def _load_raw(args) -> pd.DataFrame:
    if args.ticks:
        return load_ticks(args.ticks)
    dsn = args.database_url or os.environ.get("DATABASE_URL")
    symbol = args.symbol
    if dsn and symbol:
        return load_postgres(dsn, symbol)
    raise FileNotFoundError(
        "No tick file was provided and no database query could be run. "
        "Pass --ticks path.csv (columns: epoch or timestamp, quote or price, optional symbol) "
        "or pass --database-url and --symbol to read the ticks table. "
        "This pipeline does not invent market data."
    )


def _inspect(args) -> int:
    raw = _load_raw(args)
    _clean, report = prepare_ticks(raw, args.symbol)
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") == "ok" else 2


def _train(args) -> int:
    raw = _load_raw(args)
    overrides = {
        "symbol": args.symbol,
        "target": args.target,
        "horizon_seconds": args.horizon_seconds,
        "barrier_distance": args.barrier_distance,
        "sample_every_seconds": args.sample_every_seconds,
        "hypothetical_r": args.hypothetical_r,
    }
    if args.lstm:
        overrides["enable_lstm"] = True
    if args.fast:
        overrides["fast"] = True
    cfg = load_config(args.config, **overrides)
    quotes = load_quotes(args.quotes) if args.quotes else None
    report = run_experiment(raw, cfg, args.out, quotes=quotes)
    print(json.dumps({
        "status": report.get("status"),
        "experimental": report.get("experimental"),
        "banner": report.get("banner"),
        "tick_count": (report.get("data_quality") or {}).get("tick_count"),
        "targets": {
            name: {
                "test_opened": body.get("test_opened"),
                "labeled_examples": body.get("labeled_examples"),
                "model": (body.get("selection") or {}).get("model"),
                "test_logloss": (body.get("test_metrics") or {}).get("logloss"),
                "test_auc": (body.get("test_metrics") or {}).get("roc_auc"),
                "limitation": body.get("limitation"),
            }
            for name, body in (report.get("targets") or {}).items()
        },
    }, indent=2))
    return 0 if report.get("status") == "completed" else 1


def _infer(args) -> int:
    raw = _load_raw(args)
    bundle = load_bundle(args.model)
    if args.batch:
        records = score_batch(raw, bundle)
        destination = Path(args.out or "demo_signals.jsonl")
        destination.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        print(json.dumps({"records": len(records), "path": str(destination)}, indent=2))
        return 0
    if args.asof is None:
        _clean, report = prepare_ticks(raw, bundle.get("config", {}).get("symbol"))
        if _clean is None or _clean.empty:
            print(json.dumps(report, indent=2, default=str))
            return 2
        asof = int(_clean["epoch"].iloc[-1])
    else:
        asof = int(args.asof)
    record = score_asof(raw, bundle, asof)
    text = json.dumps(record, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
