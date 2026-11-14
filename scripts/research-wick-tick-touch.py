"""Replay nine-minute R_100 price touches after a completed five-minute wick."""

from datetime import datetime
import json
from pathlib import Path
from runpy import run_path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "research-output/c5-tick-rsi-2026-11-14/data"
OUT = ROOT / "research-output/m5-wick-push/touch.json"
MINUTES_5 = 300
HORIZON = 540
CADENCE = 2


def read_ticks():
    manifest = json.loads((RAW / "manifest.json").read_bytes())
    # Reuse the archive reader and its page, count, timestamp and hash checks.
    tick_reader = run_path(str(ROOT / "scripts/research-c5-tick-rsi.py"))["ticks"]
    timestamps, quotes, digest = tick_reader(manifest)
    cents = np.rint(100 * quotes).astype(np.int64)
    assert np.allclose(cents, 100 * quotes, rtol=0, atol=1e-6)
    return manifest, np.asarray(timestamps), cents, digest


def cluster_interval(rows):
    # Resample whole UTC days to retain overlapping horizons within each day.
    daily = {}
    for day, _, away, toward in rows:
        pair = daily.setdefault(day, [0, 0])
        pair[0] += 1
        pair[1] += int(away) - int(toward)
    if not daily:
        return None
    clusters = np.array(list(daily.values()))
    rng = np.random.default_rng(20261114)
    sums = clusters[rng.integers(len(clusters), size=(20000, len(clusters)))].sum(axis=1)
    return np.quantile(sums[:, 1] / (2 * sums[:, 0]), [0.025, 0.975]).tolist()


def paired_counts(rows):
    away = sum(int(r[2]) for r in rows)
    toward = sum(int(r[3]) for r in rows)
    both = sum(int(r[2] and r[3]) for r in rows)
    n = len(rows)
    return dict(entries=n, awayHits=away, towardHits=toward, bothHit=both,
                neither=n-away-toward+both, randomSide=(away+toward)/(2*n) if n else None,
                awayMinusRandom95=cluster_interval(rows))


def main():
    manifest, t, p, digest = read_ticks()
    start, stop = (int(datetime.fromisoformat(manifest[key]).timestamp())
                   for key in ("fromUTC", "untilUTC"))
    assert start % MINUTES_5 == stop % MINUTES_5 == 0
    checks = range(start, stop, MINUTES_5)
    breaks = np.r_[0, np.cumsum(np.diff(t) != CADENCE)]
    excluded, all_hits, events = [], [], []
    for boundary in checks:
        entry = int(t.searchsorted(boundary))
        first = entry - MINUTES_5 // CADENCE
        last = entry + HORIZON // CADENCE
        if (first < 0 or last >= len(t) or not 0 <= t[entry] - boundary < CADENCE
                or breaks[first] != breaks[last]):
            excluded.append(boundary)
            continue
        # Aggregation uses only ticks strictly before the 5-minute boundary.
        previous = p[first:entry]
        future = p[entry+1:last+1]
        high, low = previous.max(), previous.min()
        upper = high - max(previous[0], previous[-1])
        lower = min(previous[0], previous[-1]) - low
        up, down = bool(future.max() >= p[entry]+1), bool(future.min() <= p[entry]-1)
        all_hits.append((up, down))
        if upper == lower or 2 * max(upper, lower) < high - low:
            continue
        is_lower = lower > upper
        away, toward = (up, down) if is_lower else (down, up)
        events.append((boundary // 86400, "lower" if is_lower else "upper", away, toward))

    n = len(all_hits)
    total_up, total_down = (sum(pair[i] for pair in all_hits) for i in (0, 1))
    summary = dict(symbol=manifest["symbol"], entryFromUTC=manifest["fromUTC"],
                   entryUntilExclusiveUTC=manifest["untilUTC"], archivedTicks=len(t),
                   tickArchiveSHA256=digest, archiveBreaks=int(breaks[-1]),
                   earliestAndLatestDecision="UTC 5-minute boundaries in the half-open entry period",
                   wick="Wick >= half the high-low range and strictly larger than opposite wick",
                   entry="First tick at or after completed five-minute boundary",
                   outcome="Including later ticks through entry + 540 seconds, +/-0.01 price units",
                   idealFillAssumption=True, candidateBoundaries=len(checks),
                   skippedDueToGaps=excluded, eligibleBoundaries=n,
                   unconditional=dict(upHits=total_up, downHits=total_down,
                                      randomSide=(total_up+total_down)/(2*n)),
                   allWicks=paired_counts(events),
                   byWick={side: paired_counts([r for r in events if r[1] == side])
                           for side in ("lower", "upper")})
    assert n + len(excluded) == len(checks)
    assert sum(group["entries"] for group in summary["byWick"].values()) == len(events)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
