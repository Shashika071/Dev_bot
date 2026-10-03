"""Probe whether app_id / alternate WS unlocks older tick history."""
import asyncio
import json
from datetime import datetime, timezone, timedelta

import websockets


async def try_url(url: str, end: int, label: str):
    print("\n===", label, "===")
    print("url", url)
    try:
        async with websockets.connect(url, open_timeout=20) as ws:
            req = {
                "ticks_history": "R_100",
                "end": end,
                "count": 1000,
                "style": "ticks",
                "adjust_start_time": 1,
                "req_id": 1,
            }
            await ws.send(json.dumps(req))
            raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if "error" in raw:
                print("error", raw["error"])
                return
            times = (raw.get("history") or {}).get("times") or []
            print("n", len(times))
            if times:
                print(
                    "oldest",
                    datetime.fromtimestamp(min(times), tz=timezone.utc),
                    "newest",
                    datetime.fromtimestamp(max(times), tz=timezone.utc),
                    "requested_end",
                    datetime.fromtimestamp(end, tz=timezone.utc),
                )
    except Exception as e:
        print("FAIL", type(e).__name__, e)


async def main():
    # Ask for history ~3 days ago
    end = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp())
    urls = [
        ("public options", "wss://api.derivws.com/trading/v1/options/ws/public"),
        ("classic v3 app 36544", "wss://ws.derivws.com/websockets/v3?app_id=36544"),
        ("classic ws.deriv.com 36544", "wss://ws.deriv.com/websockets/v3?app_id=36544"),
        (
            "public with app_id query",
            "wss://api.derivws.com/trading/v1/options/ws/public?app_id=36544",
        ),
    ]
    for label, url in urls:
        await try_url(url, end, label)


if __name__ == "__main__":
    asyncio.run(main())
