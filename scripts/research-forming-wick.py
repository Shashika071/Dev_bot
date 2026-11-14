"""Measure the next one-minute candle after the first large wick in a forming M5."""

from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby
import json
from math import sqrt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "research-output/touch-003/download-manifest.json"
OUTPUT = ROOT / "research-output/m5-wick-push/forming.json"


def read_minutes(manifest):
    previous = None
    for page in sorted(manifest["pages"], key=lambda page: page["first"]):
        rows = json.loads((MANIFEST.parent / page["file"]).read_bytes())
        assert len(rows) == page["bars"]
        assert rows[0]["time"] == page["first"] and rows[-1]["time"] == page["last"]
        for row in rows:
            epoch = row["time"]
            assert row["isClosed"] and epoch % 60 == 0
            assert previous is None or epoch > previous
            prices = [row[key] for key in ("open", "high", "low", "close")]
            cents = [round(price * 100) for price in prices]
            assert all(abs(price * 100 - value) < 1e-6 for price, value in zip(prices, cents))
            open_, high, low, close = cents
            assert low <= min(open_, close) <= max(open_, close) <= high
            yield epoch, open_, high, low, close
            previous = epoch


def first_signal(bucket, minutes):
    expected = bucket
    open_ = minutes[0][1]
    high, low = minutes[0][2:4]
    for k, (epoch, _, minute_high, minute_low, close) in enumerate(minutes, 1):
        if k == 5 or epoch != expected:
            break
        high = max(high, minute_high)
        low = min(low, minute_low)
        upper = high - max(open_, close)
        lower = min(open_, close) - low
        if upper != lower and 2 * max(upper, lower) >= high - low:
            return k, 1 if upper > lower else -1
        expected += 60
    return None


def summary(events):
    n = len(events)
    toward = sum(score > 0 for _, _, _, score in events)
    away = sum(score < 0 for _, _, _, score in events)
    # At the same entry a fair side wins half of the non-flat candles.
    daily = defaultdict(lambda: [0, 0])
    for decision, _, _, score in events:
        day = daily[decision // 86400]
        day[0] += 1
        day[1] += score
    advantage = (toward - away) / (2 * n) if n else None
    interval = None
    if len(daily) > 1:
        variance = sum((score / 2 - advantage * count) ** 2 for count, score in daily.values())
        se = sqrt(len(daily) / (len(daily) - 1) * variance) / n
        interval = [advantage - 1.96 * se, advantage + 1.96 * se]
    return dict(events=n, toward=toward, away=away, flat=n-toward-away,
                towardRate=toward/n if n else None, awayRate=away/n if n else None,
                towardMinusFairRate=advantage, utcDays=len(daily),
                towardMinusFair95DayClusterInterval=interval)


def main():
    manifest = json.loads(MANIFEST.read_bytes())
    assert manifest["complete"] and manifest["symbol"] == "R_100" and manifest["granularity"] == 60
    start = min(page["first"] for page in manifest["pages"])
    stop = manifest["endExclusive"]
    split = start + (stop - start) * 7 // 10
    scanned = 0
    missing_next = 0
    events = []
    for bucket, group in groupby(read_minutes(manifest), key=lambda row: row[0] // 300 * 300):
        minutes = list(group)
        scanned += len(minutes)
        signal = first_signal(bucket, minutes)
        if signal is None:
            continue
        k, side = signal
        decision = bucket + k * 60
        if k >= len(minutes) or minutes[k][0] != decision:
            missing_next += 1
            continue
        _, entry, _, _, exit_ = minutes[k]
        move = (exit_ > entry) - (exit_ < entry)
        events.append((decision, k, side, move * side))
    assert scanned == manifest["bars"]
    result = dict(symbol=manifest["symbol"], archiveMinutes=scanned,
                  splitUtc=datetime.fromtimestamp(split, timezone.utc).isoformat(),
                  excludedMissingNextMinute=missing_next,
                  scope="First dominant wick >=50% of the running M5 high-low range after 1-4 closed M1 bars.",
                  outcome="Next M1 close versus its open; toward means up for upper wick, down for lower wick.",
                  groups={})
    for label, cohort in (("all", events), ("earlier", [e for e in events if e[0] < split]),
                          ("later", [e for e in events if e[0] >= split])):
        result["groups"][label] = dict(overall=summary(cohort),
                                        upper=summary([e for e in cohort if e[2] == 1]),
                                        lower=summary([e for e in cohort if e[2] == -1]),
                                        byMinute={k: summary([e for e in cohort if e[1] == k])
                                                  for k in range(1, 5)})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
