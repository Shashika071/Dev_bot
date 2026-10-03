"""
Historical tick retrieval — fetches past tick data from Deriv API and stores to DB/Parquet.

Deriv ticks_history endpoint returns up to 5000 ticks per request.
We chunk requests to cover the desired time range.
"""

import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.tick import Tick
from app.deriv.client import DerivWSClient
from app.config import settings

logger = structlog.get_logger(__name__)

# Deriv API returns max 5000 ticks per request
MAX_TICKS_PER_REQUEST = 5000
# Pause between chunk requests to respect rate limits
RATE_LIMIT_PAUSE = 1.0


class HistoryCollector:
    """
    Fetches historical tick data from Deriv API in chunks and stores to
    both PostgreSQL (for queries) and Parquet (for large dataset ML training).
    """

    def __init__(self, client: DerivWSClient, symbol: str):
        self.client = client
        self.symbol = symbol
        self._total_fetched = 0

    async def fetch_range(
        self,
        start_dt: datetime,
        end_dt: datetime,
        save_parquet: bool = True,
    ) -> int:
        """
        Fetch historical ticks for a date range.
        Returns total number of ticks fetched.
        """
        start_epoch = int(start_dt.timestamp())
        end_epoch = int(end_dt.timestamp())
        current_start = start_epoch
        chunk_num = 0

        all_ticks = []

        logger.info("history_fetch_starting",
                     symbol=self.symbol,
                     start=start_dt.isoformat(),
                     end=end_dt.isoformat())

        while current_start < end_epoch:
            chunk_num += 1
            try:
                response = await self.client.get_ticks_history(
                    symbol=self.symbol,
                    start=current_start,
                    end=end_epoch,
                    style="ticks",
                    count=MAX_TICKS_PER_REQUEST,
                )

                history = response.get("history", {})
                times = history.get("times", [])
                prices = history.get("prices", [])

                if not times or not prices:
                    logger.info("history_fetch_no_more_data",
                                chunk=chunk_num, current_start=current_start)
                    break

                # Process ticks
                chunk_ticks = []
                for epoch, price in zip(times, prices):
                    tick_time = datetime.fromtimestamp(epoch, tz=timezone.utc)
                    chunk_ticks.append({
                        "symbol": self.symbol,
                        "epoch": epoch,
                        "tick_time": tick_time,
                        "quote": float(price),
                        "is_gap": 0,
                    })

                # Store to DB
                await self._store_ticks_db(chunk_ticks)
                all_ticks.extend(chunk_ticks)
                self._total_fetched += len(chunk_ticks)

                # Move start forward
                last_epoch = max(times)
                if last_epoch <= current_start:
                    break  # No progress, stop
                current_start = last_epoch + 1

                logger.info("history_fetch_chunk",
                             chunk=chunk_num,
                             ticks=len(chunk_ticks),
                             total=self._total_fetched,
                             last_time=datetime.fromtimestamp(last_epoch, tz=timezone.utc).isoformat())

                # Rate limit
                await asyncio.sleep(RATE_LIMIT_PAUSE)

            except Exception as e:
                logger.error("history_fetch_chunk_error",
                              chunk=chunk_num, error=str(e))
                await asyncio.sleep(5)
                continue

        # Save to Parquet
        if save_parquet and all_ticks:
            await self._save_parquet(all_ticks, start_dt, end_dt)

        logger.info("history_fetch_complete",
                     symbol=self.symbol,
                     total_ticks=self._total_fetched)
        return self._total_fetched

    async def _store_ticks_db(self, ticks: list[dict]):
        """Batch insert ticks to PostgreSQL with conflict handling."""
        try:
            async with async_session() as session:
                for tick in ticks:
                    stmt = pg_insert(Tick).values(**tick).on_conflict_do_nothing(
                        index_elements=["symbol", "epoch"]
                    )
                    await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.error("history_store_db_error", error=str(e))

    async def _save_parquet(self, ticks: list[dict], start_dt: datetime, end_dt: datetime):
        """Save tick data to Parquet file for ML training."""
        try:
            os.makedirs(settings.parquet_data_dir, exist_ok=True)

            df = pd.DataFrame(ticks)
            df["tick_time"] = pd.to_datetime(df["tick_time"], utc=True)

            filename = (
                f"{self.symbol}_ticks_"
                f"{start_dt.strftime('%Y%m%d')}_"
                f"{end_dt.strftime('%Y%m%d')}.parquet"
            )
            filepath = os.path.join(settings.parquet_data_dir, filename)

            table = pa.Table.from_pandas(df)
            pq.write_table(table, filepath, compression="snappy")

            logger.info("history_parquet_saved",
                         filepath=filepath,
                         rows=len(df))
        except Exception as e:
            logger.error("history_parquet_error", error=str(e))
