"""Replay the fixed C5 raw-tick RSI protocol using the saved public history.

Run from any directory with Python and NumPy: python scripts/research-c5-tick-rsi.py
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "research-output/c5-tick-rsi-2026-11-14"
DATA = OUTPUT / "data"
OBS = ROOT / "research-output/c5-broad-screen-2026-11-12/observations.json"


def load(path):
    return json.loads(path.read_bytes())


def epoch(date):
    return int(datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp())


def ticks(protocol):
    manifest = load(DATA / "manifest.json")
    assert manifest["complete"] is True and manifest["symbol"] == protocol["symbol"]
    assert all(manifest[key] == protocol[key] for key in ("fromUTC", "untilUTC"))
    times, prices = [], []
    sha = hashlib.sha256((DATA / "manifest.json").read_bytes())
    for page in reversed(manifest["pages"]):
        raw = (DATA / page["file"]).read_bytes()
        sha.update(page["file"].encode())
        sha.update(raw)
        batch = json.loads(raw)
        assert len(batch["times"]) == len(batch["prices"]) == page["ticks"]
        assert batch["times"][0] == page["first"] and batch["times"][-1] == page["last"]
        times.extend(batch["times"])
        prices.extend(batch["prices"])
    assert len(times) == manifest["ticks"]
    t, p = np.array(times, dtype=np.int64), np.array(prices, dtype=float)
    assert np.all(np.diff(t) > 0) and np.all(np.isfinite(p)) and np.all(p > 0)
    assert t[0] <= manifest["fetchStart"] + 1 and t[-1] >= manifest["fetchEndExclusive"] - 2
    return times, p, sha.hexdigest()


def calculate_rsi(times, prices, period, warmup):
    gaps = np.flatnonzero(np.diff(times) != 2) + 1
    starts = [0, *gaps.tolist(), len(times)]
    rsi = np.full(len(times), np.nan)
    segments = np.empty(len(times), dtype=np.int32)
    for start, stop in zip(starts, starts[1:]):
        segments[start:stop] = start
        if stop - start <= period:
            continue
        changes = np.diff(prices[start:stop])
        gains = np.maximum(changes, 0)
        losses = np.maximum(-changes, 0)
        gain, loss = float(gains[:period].mean()), float(losses[:period].mean())
        for j in range(period, stop - start):
            if j > period:
                gain = (gain * (period - 1) + gains[j - 1]) / period
                loss = (loss * (period - 1) + losses[j - 1]) / period
            if j + 1 >= warmup:
                rsi[start + j] = 100 * gain / (gain + loss) if gain + loss else 50
    return rsi, segments, len(gaps)


def outcome(times, prices, rsi, index, confirmed, side):
    direction = 1 if side == "RISE" else -1
    entry_time = times[index]
    entry = float(prices[index])
    end = bisect_right(times, entry_time + 540)
    future = prices[index + 1:end]
    change = direction * (future - entry)
    reached = np.flatnonzero(change >= 0.01 - 1e-9)
    return dict(entry=entry, entryTime=entry_time, entryRsi=float(rsi[index]),
                delaySeconds=entry_time - confirmed, expiryTime=entry_time + 540,
                target=entry + direction * 0.01, hit=bool(len(reached)),
                firstTouchTime=times[index + 1 + reached[0]] if len(reached) else None,
                oppositeHit=bool(np.any(change <= -0.01 + 1e-9)))


def replay(signals, times, prices, rsi, segments):
    rows, excluded = [], []
    seen = set()
    for signal in signals:
        confirmed, side = signal["confirmedTime"], signal["side"]
        assert all(signal["flags"][key] is True for key in ("ut", "supertrend", "sma", "rsi"))
        assert confirmed == signal["signalTime"] + 60 and side in ("RISE", "FALL")
        assert (confirmed, side) not in seen
        seen.add((confirmed, side))
        first = bisect_left(times, confirmed)
        end = bisect_left(times, confirmed + 600)
        if first >= len(times) or times[first] - confirmed >= 2:
            reason = "missing entry tick"
        elif not np.isfinite(rsi[first]):
            reason = "insufficient contiguous RSI warmup"
        elif end >= len(times) or times[end] - (confirmed + 600) >= 2 or segments[first] != segments[end]:
            reason = "incomplete common 600-second window"
        else:
            reason = None
        if reason:
            excluded.append(dict(confirmedTime=confirmed, side=side, reason=reason))
            continue
        immediate = outcome(times, prices, rsi, first, confirmed, side)
        stop = bisect_left(times, confirmed + 60)
        window = rsi[first:stop]
        matches = np.flatnonzero(window <= 20 if side == "RISE" else window >= 80)
        delayed = outcome(times, prices, rsi, first + matches[0], confirmed, side) if len(matches) else None
        rows.append(dict(confirmedTime=confirmed, side=side, baseline=immediate, delayed=delayed))
    return rows, excluded


def counts(outcomes):
    n = len(outcomes)
    hits = sum(o["hit"] for o in outcomes)
    opposite = sum(o["oppositeHit"] for o in outcomes)
    return dict(n=n, hits=hits, misses=n-hits, touchRate=hits/n if n else None,
                randomSideRate=(hits+opposite)/(2*n) if n else None)


def summarize(rows):
    kept = [r for r in rows if r["delayed"] is not None]
    skipped = [r for r in rows if r["delayed"] is None]
    transitions = Counter((r["baseline"]["hit"], r["delayed"]["hit"]) for r in kept)
    return dict(baseline=counts([r["baseline"] for r in rows]),
                matched=counts([r["baseline"] for r in kept]), delayed=counts([r["delayed"] for r in kept]),
                skipped=counts([r["baseline"] for r in skipped]),
                medianDelaySeconds=median(r["delayed"]["delaySeconds"] for r in kept) if kept else None,
                missToHit=transitions[False, True], hitToMiss=transitions[True, False],
                hitToHit=transitions[True, True], missToMiss=transitions[False, False])


def bootstrap(rows):
    days = {}
    for row in rows:
        v = days.setdefault(row["confirmedTime"] // 86400, np.zeros(6, dtype=np.int64))
        base, delayed = row["baseline"], row["delayed"]
        v[0] += 1
        v[1] += base["hit"]
        if delayed is not None:
            v[2:] += [1, base["hit"], delayed["hit"], delayed["oppositeHit"]]
    values = np.array([days[day] for day in sorted(days)])
    rng = np.random.default_rng(20261114)
    indices = rng.integers(len(values), size=(20000, len(values)))
    totals = values[indices].sum(axis=1)
    n, hits, triggered, matched, delayed, opp = totals[totals[:, 2] > 0].T
    def interval(x, alpha):
        return np.quantile(x, [alpha / 2, 1 - alpha / 2]).tolist() if len(x) else None
    return dict(days=len(values), draws=20000, validDraws=len(n), seed=20261114,
                combinedAdjusted975=interval(delayed/triggered-hits/n, 0.025),
                timingAdjusted975=interval((delayed-matched)/triggered, 0.025),
                randomSideEdge95=interval((delayed-opp)/(2*triggered), 0.05))


def main():
    protocol = load(OUTPUT / "protocol.json")
    assert (protocol["symbol"], protocol["rsiPeriod"], protocol["rsiWarmupTicks"],
            protocol["tickSeconds"], protocol["waitSeconds"], protocol["barrier"],
            protocol["horizonSeconds"]) == ("R_100", 10, 260, 2, 60, 0.01, 540)
    start, stop = epoch(protocol["fromUTC"]), epoch(protocol["untilUTC"])
    times, prices, source_hash = ticks(protocol)
    original = load(OBS)
    signals = [s for s in original if start <= s["confirmedTime"] < stop]
    rsi, segments, gap_count = calculate_rsi(times, prices, 10, 260)
    rows, excluded = replay(signals, times, prices, rsi, segments)
    s = summarize(rows)
    by_side = {d: summarize([r for r in rows if r["side"] == d]) for d in ("RISE", "FALL")}
    ci = bootstrap(rows)
    report = dict(protocol=protocol, ticks=len(times), gaps=gap_count, signals=len(signals),
                  eligible=len(rows), excluded=excluded, summary=s, bySide=by_side, intervals=ci,
                  tickSourceSHA256=source_hash, signalSourceSHA256=hashlib.sha256(OBS.read_bytes()).hexdigest())
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUTPUT / "observations.json").write_text(json.dumps(rows), encoding="utf-8")
    def percent(x):
        return f"{100*x:.2f}%" if x is not None else "n/a"
    def band(x):
        return f"{100*x[0]:+.2f} to {100*x[1]:+.2f} percentage points" if x is not None else "n/a"
    results = ["# C5 entries timed with tick RSI(10)", "",
               f"October 1–30, 2026 UTC. The replay used {len(times):,} public R_100 ticks and {len(rows):,} eligible closed-minute C5 signals. {len(excluded)} additional signals lacked the required tick coverage.", "",
               "RISE enters on the first tick with RSI(10) ≤20 after confirmation. FALL enters on the first tick with RSI(10) ≥80. A qualifying first tick counts. The entry window ends immediately before the next one-minute close. The ±0.01 barrier and nine-minute expiry start at the qualifying quote.", "",
               "| Entry | Entries | Touches | Misses | Touch rate |",
               "|---|---:|---:|---:|---:|"]
    for label, key in [("Immediate C5", "baseline"), ("Immediate entry on signals that later met the RSI rule", "matched"), ("RSI entry", "delayed")]:
        x = s[key]
        results.append(f"| {label} | {x['n']} | {x['hits']} | {x['misses']} | {percent(x['touchRate'])} |")
    results += ["", f"RSI skipped {s['skipped']['n']} signals. At immediate entry {s['skipped']['hits']} of those would have touched and {s['skipped']['misses']} would have missed. On signals that qualified, waiting changed {s['missToHit']} misses into touches and {s['hitToMiss']} touches into misses. Median entry delay was {s['medianDelaySeconds']} seconds.", "",
                f"The middle row uses hindsight to identify which signals eventually reached the RSI threshold. It measures the effect of entry timing on the same signals. Random direction at the delayed entry times averaged {percent(s['delayed']['randomSideRate'])} touches.", "",
                f"An analysis resampling whole UTC days in 20,000 paired draws put the adjusted 97.5% interval for the combined change at {band(ci['combinedAdjusted975'])}. For waiting on the same signals it was {band(ci['timingAdjusted975'])}. The intervals assume independent days. With zero observed RSI misses, resampling cannot generate an RSI miss and can understate the uncertainty about future performance.", "",
                "The replay assumes an immediate fill at the quote that reveals the RSI level. It measures subsequent price touches. Actual entry latency, contract payouts, settlement and overlapping positions can change trading results. The candle archive was previously inspected.", "",
                "The source manifest and individual ticks are in the data directory. The fixed rules are in protocol.json. report.json contains the counts by direction and observations.json contains each individual entry."]
    (OUTPUT / "report.md").write_text("\n".join(results)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("protocol","excluded")}, indent=2))


if __name__ == "__main__":
    main()
