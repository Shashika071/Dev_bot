"""Score the M1 CCI/MACD/SAR arrows against the most recent closed M5 wick."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from lib.cci_macd_sar import signals


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'research-output/touch-003/download-manifest.json'
OUTPUT = ROOT / 'research-output/m5-wick-push/strategy.json'
GROUPS = ('all', 'awayFromTip', 'towardTip', 'noLargeWick')


def load_candles():
    manifest = json.loads(MANIFEST.read_bytes())
    assert manifest['symbol'] == 'R_100' and manifest['complete'] and manifest['granularity'] == 60
    candles = []
    for page in sorted(manifest['pages'], key=lambda item: item['first']):
        bars = json.loads((MANIFEST.parent / page['file']).read_bytes())
        assert len(bars) == page['bars'] and (bars[0]['time'], bars[-1]['time']) == (page['first'], page['last'])
        candles.extend(bars)
    assert len(candles) == manifest['bars']
    assert all(bar['isClosed'] and bar['low'] <= bar['open'] <= bar['high'] for bar in candles)
    return candles


def wick_side(bars):
    # Using the feed's integer cents makes exactly half the range inclusive.
    high = round(100 * max(c['high'] for c in bars))
    low = round(100 * min(c['low'] for c in bars))
    opening = round(100 * bars[0]['open'])
    closing = round(100 * bars[-1]['close'])
    upper = high - max(opening, closing)
    lower = min(opening, closing) - low
    if upper > lower and 2 * upper >= high - low:
        return 'RISE'
    if lower > upper and 2 * lower >= high - low:
        return 'FALL'
    return None


def summarize(rows):
    counts = np.zeros((len(GROUPS), 3), dtype=int)
    days = defaultdict(lambda: np.zeros((len(GROUPS), 2), dtype=int))
    for timestamp, group, signed_change, _ in rows:
        for index in (0, GROUPS.index(group)):
            counts[index] += [1, signed_change > 0, signed_change == 0]
            days[timestamp // 86400][index] += [1, signed_change > 0]
    assert np.array_equal(counts[1:].sum(axis=0), counts[0])
    # Resample the same UTC days for the subset and the all-arrows baseline.
    daily = np.stack(list(days.values()))
    rng = np.random.default_rng(20261114)
    weights = rng.multinomial(len(daily), np.full(len(daily), 1 / len(daily)), size=10000)
    totals = (weights @ daily.reshape(len(daily), -1)).reshape(-1, len(GROUPS), 2)
    rates = np.divide(totals[:, :, 1], totals[:, :, 0], where=totals[:, :, 0] > 0,
                      out=np.full(totals[:, :, 1].shape, np.nan))
    result = {}
    for i, group in enumerate(GROUPS):
        n, hits, ties = map(int, counts[i])
        result[group] = dict(n=n, correct=hits, wrong=n-hits-ties, ties=ties,
                             correctRate=hits/n if n else None,
                             deltaVsAll95=np.nanquantile(rates[:, i]-rates[:, 0], [0.025, 0.975]).tolist())
    return result


def touch_outcome(candles, i, side):
    future = candles[i+1:i+10]
    if len(future) != 9 or future[-1]['time'] != candles[i]['time'] + 540:
        return None
    entry = round(100 * future[0]['open'])
    up = round(100 * max(bar['high'] for bar in future)) >= entry + 1
    down = round(100 * min(bar['low'] for bar in future)) <= entry - 1
    return (up, down) if side == 'RISE' else (down, up)


def summarize_touches(rows):
    result = {}
    for group in GROUPS:
        observed = [pair for _, category, _, pair in rows
                    if pair is not None and (group == 'all' or category == group)]
        n = len(observed)
        hits = sum(hit for hit, _ in observed)
        opposite_hits = sum(opposite for _, opposite in observed)
        result[group] = dict(n=n, hits=hits, misses=n-hits, touchRate=hits/n if n else None,
                             oppositeHits=opposite_hits, randomSideRate=(hits+opposite_hits)/(2*n) if n else None)
    return result


def main():
    candles = load_candles()
    index_by_time = {bar['time']: i for i, bar in enumerate(candles)}
    first, end = candles[0]['time'], candles[-1]['time'] + 60
    split = first + (end-first) * 7 // 10
    rows = []
    arrows = exclusions = 0
    for i, side in signals(candles):
        arrows += 1
        confirmation = candles[i]['time'] + 60
        bucket_end = confirmation // 300 * 300
        j = index_by_time.get(bucket_end-300)
        if (j is None or j+4 > i or candles[j+4]['time'] != bucket_end-60 or
                i+1 == len(candles) or candles[i+1]['time'] != confirmation):
            exclusions += 1
            continue
        tip = wick_side(candles[j:j+5])
        group = 'noLargeWick' if tip is None else ('towardTip' if side == tip else 'awayFromTip')
        future = candles[i+1]
        change = round(100 * future['close']) - round(100 * future['open'])
        rows.append((confirmation, group, change if side == 'RISE' else -change,
                     touch_outcome(candles, i, side)))
    assert len(rows) + exclusions == arrows
    def utc(ts):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    earlier = [row for row in rows if row[0] < split]
    later = [row for row in rows if row[0] >= split]
    result = dict(symbol='R_100', timeframe='1m', bars=len(candles), fromUTC=utc(first),
                  splitUTC=utc(split), endExclusiveUTC=utc(end), signals=arrows, exclusions=exclusions,
                  signal='CCI(20 HLC3) zero and MACD(12,26,9) signal crosses on same M1 close; SAR(0.02,0.02,0.20) agrees.',
                  wick='Last completed UTC M5 at M1 confirmation. One wick >=50% of H-L and strictly longer than opposite wick.',
                  endpoint='Sign of next M1 close minus open in arrow direction. Flat counts as non-success and is shown separately.',
                  interval='10,000 paired UTC-day bootstrap draws per period, difference in correct rates vs all arrows.',
                  earlier=summarize(earlier), later=summarize(later),
                  touchDescription='Secondary M1 proxy: directional excursion 0.01 from next open within next nine consecutive candle ranges. Random-side rate averages the two directions at the same entries. No tick timing or payouts.',
                  earlierTouches=summarize_touches(earlier), laterTouches=summarize_touches(later))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
