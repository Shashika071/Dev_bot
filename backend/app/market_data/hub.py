"""In-process candle cache fed by the worker and read by the API."""

from __future__ import annotations

import time
from typing import Any

from app.signal_engine.chart_candles import INTERVAL_SECONDS, aggregate_ohlc


class MarketDataHub:
    def __init__(self) -> None:
        self._candles: dict[tuple[str, str], list[dict]] = {}
        self._ticks: dict[str, list[dict]] = {}
        self._updated: dict[tuple[str, str], float] = {}
        self._last_tick: dict[str, dict] = {}

    def ingest_ticks(self, symbol: str, points: list[tuple[int, float]], *, source: str = "ticks") -> None:
        if not points:
            return
        symbol = str(symbol)
        merged = {
            int(row["epoch"]): float(row["quote"])
            for row in self._ticks.get(symbol, [])
        }
        for epoch, quote in points:
            merged[int(epoch)] = float(quote)
        ordered = sorted(merged.items())[-2000:]
        self._ticks[symbol] = [
            {"epoch": epoch, "quote": quote} for epoch, quote in ordered
        ]
        self._last_tick[symbol] = self._ticks[symbol][-1]
        self._touch_forming_candle(symbol, int(ordered[-1][0]), float(ordered[-1][1]))
        for interval in INTERVAL_SECONDS:
            candles = aggregate_ohlc(ordered, interval, max_candles=500)
            if not candles or self.fresh(symbol, interval, max_age=20):
                continue
            existing = self._candles.get((symbol, interval)) or []
            if existing and str(existing[-1].get("source") or "") == "deriv_official" and len(existing) > len(candles):
                continue
            self._store(symbol, interval, candles, source)

    def ingest_candles(
        self,
        symbol: str,
        interval: str,
        candles: list[dict],
        *,
        source: str = "deriv_official",
    ) -> None:
        if interval not in INTERVAL_SECONDS or not candles:
            return
        cleaned = []
        for row in candles:
            cleaned.append({
                "epoch": int(row["epoch"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "ticks": row.get("ticks"),
                "source": source,
            })
        cleaned.sort(key=lambda item: item["epoch"])
        if source != "deriv_official":
            existing = self._candles.get((symbol, interval)) or []
            if existing and str(existing[-1].get("source") or "") == "deriv_official" and len(existing) >= len(cleaned):
                return
        self._store(symbol, interval, cleaned[-1200:], source)
        tick = self._last_tick.get(symbol)
        if tick:
            self._touch_forming_candle(symbol, int(tick["epoch"]), float(tick["quote"]))

    def _store(self, symbol: str, interval: str, candles: list[dict], source: str) -> None:
        key = (symbol, interval)
        stamped = []
        step = INTERVAL_SECONDS[interval]
        now = int(time.time())
        for row in candles:
            item = dict(row)
            item["source"] = item.get("source") or source
            item["is_closed"] = int(item["epoch"]) + step <= now
            stamped.append(item)
        self._candles[key] = stamped
        self._updated[key] = time.time()

    def _touch_forming_candle(self, symbol: str, epoch: int, price: float) -> None:
        """Move the current candle with the latest tick. Closed history stays put."""
        for interval, step in INTERVAL_SECONDS.items():
            rows = self._candles.get((symbol, interval))
            if not rows:
                continue
            last = rows[-1]
            last_epoch = int(last["epoch"])
            if epoch < last_epoch:
                continue
            bucket = (int(epoch) // step) * step
            if last_epoch == bucket:
                last["high"] = max(float(last["high"]), price)
                last["low"] = min(float(last["low"]), price)
                last["close"] = price
                last["is_closed"] = False
            elif epoch >= last_epoch + step:
                rows.append({
                    "epoch": bucket,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "source": last.get("source") or "ticks",
                    "is_closed": False,
                })
            else:
                continue
            self._updated[(symbol, interval)] = time.time()

    def candles(self, symbol: str, interval: str, limit: int = 300) -> list[dict]:
        rows = list(self._candles.get((symbol, interval)) or [])
        if limit > 0:
            rows = rows[-limit:]
        return rows

    def fresh(self, symbol: str, interval: str, max_age: float = 30.0) -> bool:
        updated = self._updated.get((symbol, interval))
        return updated is not None and (time.time() - updated) <= max_age and len(self.candles(symbol, interval)) >= 8

    def snapshot(self, symbol: str) -> dict[str, Any]:
        tick = self._last_tick.get(symbol)
        intervals = {}
        for interval in INTERVAL_SECONDS:
            rows = self.candles(symbol, interval, limit=2)
            intervals[interval] = {
                "count": len(self._candles.get((symbol, interval)) or []),
                "fresh": self.fresh(symbol, interval),
                "last_epoch": rows[-1]["epoch"] if rows else None,
                "last_close": rows[-1]["close"] if rows else None,
            }
        age = None
        if tick:
            age = max(0.0, time.time() - float(tick["epoch"]))
        return {
            "schema_version": 1,
            "symbol": symbol,
            "last_tick": tick,
            "tick_age_seconds": age,
            "intervals": intervals,
            "stale": tick is None or (age is not None and age > 30),
        }


market_hub = MarketDataHub()
