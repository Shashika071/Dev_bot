"""Does the dominant wick of a closed R_100 five-minute candle predict the next M1 bar?"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "research-output/touch-003/download-manifest.json"
OUT = ROOT / "research-output/m5-wick-push"


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def load_minutes():
    manifest = json.loads(MANIFEST.read_bytes())
    assert manifest["complete"] and manifest["granularity"] == 60 and manifest["symbol"] == "R_100"
    times = np.empty(manifest["bars"], dtype=np.int64)
    prices = np.empty((manifest["bars"], 4), dtype=np.int64)
    offset = 0
    for page in sorted(manifest["pages"], key=lambda item: item["first"]):
        bars = json.loads((MANIFEST.parent / page["file"]).read_bytes())
        assert len(bars) == page["bars"] and all(b["isClosed"] for b in bars)
        assert bars[0]["time"] == page["first"] and bars[-1]["time"] == page["last"]
        chunk = slice(offset, offset + len(bars))
        times[chunk] = [b["time"] for b in bars]
        quotes = np.array([[b[k] for k in ("open", "high", "low", "close")] for b in bars]) * 100
        assert np.all(np.abs(quotes - np.rint(quotes)) < 1e-7)
        prices[chunk] = np.rint(quotes).astype(np.int64)
        offset += len(bars)
    assert offset == len(times) and np.all(times % 60 == 0) and np.all(np.diff(times) > 0)
    op, hi, lo, cl = prices.T
    assert np.all(lo <= np.minimum(op, cl)) and np.all(hi >= np.maximum(op, cl))
    return times, prices


def day_interval(days, margin):
    # Paired difference against a fair random side on the same entries. A flat close
    # gives both sides zero. Resampling whole UTC days keeps within-day events together.
    _, index = np.unique(days, return_inverse=True)
    n = np.bincount(index)
    total_margin = np.bincount(index, weights=margin)
    rng = np.random.default_rng(20261114)
    draws = []
    for _ in range(20):
        picks = rng.integers(len(n), size=(500, len(n)))
        draws.extend(50 * total_margin[picks].sum(axis=1) / n[picks].sum(axis=1))
    return np.quantile(draws, [0.025, 0.975]).tolist()


def summarize(days, tip_dir, body, excursion, mask, interval=False):
    moves = body[mask] * tip_dir[mask]
    extremes = excursion[mask] * tip_dir[mask]
    n = len(moves)
    toward, away = int(np.sum(moves > 0)), int(np.sum(moves < 0))
    result = dict(n=n, toward=toward, away=away, flat=n-toward-away,
                  towardRate=toward/n, awayRate=away/n, randomSideRate=(toward+away)/(2*n),
                  largerExcursionToward=int(np.sum(extremes > 0)),
                  largerExcursionAway=int(np.sum(extremes < 0)), equalExcursions=int(np.sum(extremes == 0)),
                  meanTowardSignedMovePoints=float(moves.mean()/100))
    if interval:
        result["towardMinusRandom95pp"] = day_interval(days[mask], np.sign(moves))
    return result


def main():
    times, prices = load_minutes()
    split = int(times[0]) + (int(times[-1])+60-int(times[0]))*0.70
    # Test each aligned 5m start only if all five component bars and the next
    # M1 candle exist. Strictly increasing minute-aligned timestamps make the
    # endpoint check sufficient to rule out internal gaps.
    start = np.flatnonzero(times[:-5] % 300 == 0)
    start = start[times[start+5] == times[start]+300]
    decision = times[start+5]
    opens, highs, lows, closes = prices.T
    high = np.maximum.reduce([highs[start+k] for k in range(5)])
    low = np.minimum.reduce([lows[start+k] for k in range(5)])
    op, cl = opens[start], closes[start+4]
    top = high - np.maximum(op, cl)
    bottom = np.minimum(op, cl) - low
    span, wick = high-low, np.maximum(top, bottom)
    tip_dir = np.sign(top-bottom)
    nxt = start+5
    body = closes[nxt] - opens[nxt]
    excursion = (highs[nxt] - opens[nxt]) - (opens[nxt] - lows[nxt])
    # One extra robustness check for a visually long wick: compare its absolute
    # length with the median full high-low range of the previous 20 closed M5 bars.
    previous_median = np.full(len(span), np.nan)
    previous_median[20:] = np.median(np.lib.stride_tricks.sliding_window_view(span[:-1], 20), axis=1)
    previous_median[20:] = np.where(decision[20:]-decision[:-20] == 6000, previous_median[20:], np.nan)
    primary = (tip_dir != 0) & (span > 0) & (2*wick >= span)
    cohorts = {
        "dominant50": primary,
        "upper50": primary & (tip_dir == 1),
        "lower50": primary & (tip_dir == -1),
        "dominant60": primary & (5*wick >= 3*span),
        "dominant70": primary & (10*wick >= 7*span),
        "alsoAtLeastPriorMedianRange": primary & (wick >= previous_median),
    }
    result = {"symbol":"R_100", "bars":len(times), "from":utc(int(times[0])),
              "endExclusive":utc(int(times[-1])+60), "split":utc(split), "splits":{}}
    for name, era in {"full":np.ones(len(start), dtype=bool),
                      "earlier":decision < split, "later":decision >= split}.items():
        benchmark = body[era]
        part = {"allFiveMinuteBoundaries":{"n":len(benchmark), "up":int(np.sum(benchmark>0)),
                                           "down":int(np.sum(benchmark<0)), "flat":int(np.sum(benchmark==0))}}
        for cohort, events in cohorts.items():
            part[cohort] = summarize(decision//86400, tip_dir, body, excursion,
                                     era & events, interval=cohort in {"dominant50", "alsoAtLeastPriorMedianRange"})
        result["splits"][name] = part
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"closed.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v["dominant50"] for k,v in result["splits"].items()}, indent=2))


if __name__ == "__main__":
    main()
