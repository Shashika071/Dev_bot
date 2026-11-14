"""Replay the default TradingView signal on the saved R_100 M1 and tick histories."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from lib.cci_macd_sar import signals
from lib.cci_macd_sar_reversals import replay_reversals

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research-output/cci-macd-sar-2026-11-14"
CANDLES = ROOT / "research-output/touch-003/download-manifest.json"
TICKS = ROOT / "research-output/c5-tick-rsi-2026-11-14/data/manifest.json"
PINE = ROOT / "tradingview/v100-cci-macd-sar.pine"


def read_pages(path):
    raw = path.read_bytes()
    manifest = json.loads(raw)
    assert manifest["complete"] and manifest["symbol"] == "R_100"
    digest = sha256(raw)
    for page in sorted(manifest["pages"], key=lambda p: p["first"]):
        raw = (path.parent / page["file"]).read_bytes()
        digest.update(page["file"].encode())
        digest.update(raw)
        yield page, json.loads(raw), digest


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def cents(price):
    value = round(100 * price)
    assert price > 0 and abs(100 * price - value) < 1e-6
    return value


def first_or_later_end(candles, index):
    # Nine complete future minutes; the entry is the open of the first.
    window = candles[index+1:index+10]
    if len(window) != 9 or window[-1]["time"] != candles[index]["time"] + 540:
        return None
    entry = cents(window[0]["open"])
    return (cents(max(c["high"] for c in window)) >= entry+1,
            cents(min(c["low"] for c in window)) <= entry-1)


def score(side, up, down):
    return (up, down) if side == "RISE" else (down, up)


def counts(rows):
    n = len(rows)
    hits = sum(row[0] for row in rows)
    opposite = sum(row[1] for row in rows)
    return dict(n=n, hits=hits, misses=n-hits, oppositeHits=opposite,
                touchRate=hits/n if n else None,
                randomSideRate=(hits+opposite)/(2*n) if n else None)


def day_interval(rows):
    # Paired comparison with a fair random side at the same entry timestamps.
    days = defaultdict(lambda: [0, 0])
    for row in rows:
        hit, opposite = row["primary"]
        day = days[row["confirmedTime"]//86400]
        day[0] += 1
        day[1] += int(hit) - int(opposite)
    if not days:
        return None
    clusters = np.array(list(days.values()))
    rng = np.random.default_rng(20261114)
    totals = clusters[rng.integers(len(days), size=(20000, len(days)))].sum(axis=1)
    return np.quantile(totals[:, 1]/(2*totals[:, 0]), [.025, .975]).tolist()


def tick_outcome(times, prices, processed, side):
    entry = bisect_right(times, processed)
    end = bisect_right(times, processed + 540)
    later = prices[entry+1:end]
    up = max(later) >= prices[entry]+1
    down = min(later) <= prices[entry]-1
    return score(side, up, down)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    candles = []
    for page, batch, candle_hash in read_pages(CANDLES):
        assert len(batch) == page["bars"] and batch[0]["time"] == page["first"] and batch[-1]["time"] == page["last"]
        candles.extend(batch)
    assert len(candles) == json.loads(CANDLES.read_bytes())["bars"]
    assert all(c["isClosed"] and c["time"]%60 == 0 and c["low"] <= min(c["open"],c["close"]) <= max(c["open"],c["close"]) <= c["high"] for c in candles)
    assert all(a["time"] < b["time"] for a,b in zip(candles,candles[1:]))
    arrows = list(signals(candles, warmup=260))
    assert arrows and len({i for i,_ in arrows}) == len(arrows)
    reversals = replay_reversals(candles, arrows)
    all_rows = []
    for i, side in arrows:
        result = first_or_later_end(candles,i)
        all_rows.append({"confirmedTime":candles[i]["time"]+60, "side":side,
                         "candleTouch":score(side,*result) if result is not None else None})
    year = counts([r["candleTouch"] for r in all_rows if r["candleTouch"] is not None])

    times, prices = [], []
    for page,batch,tick_hash in read_pages(TICKS):
        assert len(batch["prices"]) == len(batch["times"]) == page["ticks"]
        assert batch["times"][0] == page["first"] and batch["times"][-1] == page["last"]
        times.extend(batch["times"])
        prices.extend(cents(v) for v in batch["prices"])
    manifest = json.loads(TICKS.read_bytes())
    assert len(times) == manifest["ticks"] and all(a < b for a,b in zip(times,times[1:]))
    start, stop = [int(datetime.fromisoformat(manifest[k].replace("Z","+00:00")).timestamp()) for k in ("fromUTC","untilUTC")]
    breaks = np.r_[0,np.cumsum(np.diff(times)!=2)]
    checked = 0
    for c in candles:
        if start <= c["time"] < stop:
            i = bisect_left(times,c["time"])
            j = bisect_left(times,c["time"]+60)
            if j-i == 30 and breaks[i] == breaks[j-1]:
                quotes = prices[i:j]
                assert [quotes[0],max(quotes),min(quotes),quotes[-1]] == [cents(c[k]) for k in ("open","high","low","close")]
                checked += 1
    tick_rows, excluded = [], []
    for row in all_rows:
        t, side = row["confirmedTime"], row["side"]
        if not start <= t < stop:
            continue
        i = bisect_left(times,t)-1
        j = bisect_left(times,t+542)
        if i < 0 or j == len(times) or breaks[i] != breaks[j] or not 0 < t-times[i] <= 2 or not 0 <= times[j]-t-542 < 2:
            excluded.append(t)
            continue
        tick_rows.append({"confirmedTime":t,"side":side,"primary":tick_outcome(times,prices,t,side),
                          "extraDelay":tick_outcome(times,prices,t+2,side)})
    primary = counts([r["primary"] for r in tick_rows])
    extra = counts([r["extraDelay"] for r in tick_rows])
    by_side = {s:counts([r["primary"] for r in tick_rows if r["side"]==s]) for s in ("RISE","FALL")}
    interval = day_interval(tick_rows)
    rep = dict(firstM1=iso(candles[0]["time"]),endExclusiveM1=iso(candles[-1]["time"]+60),
               candles=len(candles),arrows=len(arrows),candle=year,reversals=reversals,
               tickPeriod=[iso(start),iso(stop)],ticksWithBuffers=len(times),completeMinutesMatched=checked,
               gapExclusions=excluded,primary=primary,extraProcessingDelay2s=extra,bySide=by_side,
               primaryEdge95=interval,candleHash=candle_hash.hexdigest(),tickHash=tick_hash.hexdigest(),
               pineHash=sha256(PINE.read_bytes()).hexdigest())
    (OUT/"report.json").write_text(json.dumps(rep,indent=2),encoding="utf-8")
    (OUT/"signals.json").write_text(json.dumps(all_rows,separators=(",",":")),encoding="utf-8")
    (OUT/"tick-outcomes.json").write_text(json.dumps(tick_rows,separators=(",",":")),encoding="utf-8")
    def table(label,s):
        return f"| {label} | {s['n']:,} | {s['hits']:,} | {s['misses']:,} | {s['touchRate']:.2%} | {s['randomSideRate']:.2%} |"
    lines = [
        "# R_100 one-minute CCI, MACD and Parabolic SAR replay","",
        "CCI(20) uses the average of high, low and close. On a completed candle, BUY requires CCI to cross above zero and MACD(12,26,9) to cross above its signal line, with SAR below the close. SELL requires both downward crosses with SAR above the close. SAR starts at 0.02, increases by 0.02, and caps at 0.20. Both crosses must occur on the same candle. Every arrow is a separate opportunity, including overlapping nine-minute windows.","",
        "| Sample and entry | Signals | Touches | Misses | Directional touch rate | Random side at the same entries |",
        "|---|---:|---:|---:|---:|---:|",table("Full M1 archive, next open",year),
        table("October ticks, order processed at confirmation",primary),table("October ticks, processing two seconds later",extra),
        table("October RISE, processing at confirmation",by_side["RISE"]),table("October FALL, processing at confirmation",by_side["FALL"]),"",
        f"The M1 archive covers {rep['firstM1']} through {rep['endExclusiveM1']} (exclusive) and contains {len(candles):,} bars. Indicators restart after a missing minute and get a 260-bar warmup. {len(arrows)-year['n']} arrows had incomplete nine-minute candle outcomes. The candle proxy compares the next open with the extremes of the following nine whole candles.","",
        f"The October tick check covers {iso(start)} through {iso(stop)} (exclusive) within the larger archive. {checked:,} complete tick-derived minutes matched the source candles. {len(excluded)} October signals had incomplete common tick coverage. For both timing assumptions, the fill is the first tick strictly after processing. The target is one cent from that fill in the signal direction. The expiry is nine minutes after processing. Only subsequent quotes through expiry can touch. Actual server delays, purchases and payouts are unavailable. [Deriv trading terms](https://deriv.com/terms-and-conditions/trading-terms)","",
        f"The primary tick sample shows a {(primary['touchRate']-primary['randomSideRate'])*100:+.2f} percentage point difference versus random direction. Its approximate 95% interval is {100*interval[0]:+.2f} to {100*interval[1]:+.2f} points from 20,000 paired UTC-day resamples. Days are assumed independent. The two entry delays are applied to the same signals. The October sample was previously reviewed.","",
        "## TradingView reversal strategy","",
        f"A separate replay of the Pine strategy's opposite-signal exit closed {reversals['closed_trades']:,} simulated one-unit positions on the M1 archive. {reversals['winning_trades']:,} gained, {reversals['losing_trades']:,} lost and {reversals['flat_trades']:,} finished flat. Realized change was {reversals['net_realized_points']:+,.2f} price points before costs. Same-direction arrows leave the position open. The next opposite arrow reverses at the following open. Segment-end open positions are reported separately in report.json.","",
        "This local reconstruction uses the Pine file's default settings. Exact signal parity with TradingView depends on its feed and available history and has not been verified in its compiler. The One Touch figures measure historical price touches and need actual stakes, payouts and settlements to evaluate returns.","",
        "Reproduce with `python scripts/research-cci-macd-sar.py`. Signal timestamps and individual tick outcomes are saved beside this report."
    ]
    (OUT/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in rep.items() if k not in ["gapExclusions","reversals"]},indent=2))
    print("Reversal trades and net points",reversals["closed_trades"],reversals["net_realized_points"])

if __name__ == "__main__":
    main()
