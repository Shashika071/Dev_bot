import asyncio
import os
from datetime import datetime, timezone

import websockets
import json


async def main():
    # Classic Deriv WS (may need app_id)
    url = "wss://ws.derivws.com/websockets/v3?app_id=36544"
    async with websockets.connect(url) as ws:
        end = int(datetime.now(timezone.utc).timestamp()) - 2 * 86400
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
            print("v3 error", raw["error"])
            return
        times = (raw.get("history") or {}).get("times") or []
        print("v3 n", len(times))
        if times:
            print(
                "oldest",
                datetime.fromtimestamp(min(times), tz=timezone.utc),
                "newest",
                datetime.fromtimestamp(max(times), tz=timezone.utc),
                "requested_end",
                datetime.fromtimestamp(end, tz=timezone.utc),
            )


if __name__ == "__main__":
    asyncio.run(main())
