"""Replay a frozen Touch decision log without placing orders."""

from __future__ import annotations

import argparse
import json

import pandas as pd

from app.ml.replay import TouchReplayRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Touch decisions from a JSON tick file")
    parser.add_argument("--ticks", required=True, help="JSON list of {epoch, tick_time, quote}")
    parser.add_argument("--entries", required=True, help="JSON list of entry epochs")
    parser.add_argument("--direction", default="upper")
    parser.add_argument("--barrier", type=float, default=0.09)
    parser.add_argument("--duration", type=int, default=540)
    args = parser.parse_args()
    ticks = pd.read_json(args.ticks)
    entries = json.loads(open(args.entries, encoding="utf-8").read())
    report = TouchReplayRunner(duration_seconds=args.duration).run(
        ticks,
        entry_epochs=[int(item) for item in entries],
        barrier_distance=args.barrier,
        direction=args.direction,
    )
    print(json.dumps({"n": report["n"], "would_signal": report["would_signal"]}, indent=2))


if __name__ == "__main__":
    main()
