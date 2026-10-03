import asyncio
from datetime import datetime, timezone

from app.deriv.client import DerivWSClient


async def main():
    c = DerivWSClient()
    await c.connect()
    end = int(datetime.now(timezone.utc).timestamp())
    empty_streak = 0
    for i in range(60):
        resp = await c.get_ticks_history("R_100", end=end, count=1000, style="ticks")
        err = resp.get("error")
        hist = resp.get("history") or {}
        times = hist.get("times") or []
        if err:
            print(i, "ERROR", err, "end", datetime.fromtimestamp(end, tz=timezone.utc))
            end -= 3600
            empty_streak += 1
            if empty_streak >= 5:
                break
            continue
        if not times:
            print(i, "EMPTY end=", datetime.fromtimestamp(end, tz=timezone.utc))
            end -= 6 * 3600
            empty_streak += 1
            if empty_streak >= 5:
                break
            continue
        empty_streak = 0
        oldest, newest = min(times), max(times)
        print(
            i,
            "n=",
            len(times),
            "oldest",
            datetime.fromtimestamp(oldest, tz=timezone.utc),
            "newest",
            datetime.fromtimestamp(newest, tz=timezone.utc),
        )
        end = oldest - 1
        await asyncio.sleep(0.25)

    end = int(datetime.now(timezone.utc).timestamp())
    start = end - 3 * 86400
    resp = await c.get_ticks_history(
        "R_100", end=end, start=start, count=5000, style="candles"
    )
    candles = resp.get("candles") or []
    print("candles3d", len(candles), "err", resp.get("error"))
    if candles:
        print(
            "candle first",
            datetime.fromtimestamp(candles[0]["epoch"], tz=timezone.utc),
            "last",
            datetime.fromtimestamp(candles[-1]["epoch"], tz=timezone.utc),
        )
    await c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
