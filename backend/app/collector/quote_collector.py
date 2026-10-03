"""
Quote collector — periodically fetches Touch contract proposals (quotes)
from Deriv API without purchasing, storing pricing data for EV calculations.

This NEVER places trades — only requests proposal pricing.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.quote import Quote
from app.models.settings import ContractSettings
from app.deriv.client import DerivWSClient
from app.deriv import CONTRACT_ONETOUCH

logger = structlog.get_logger(__name__)

# Default polling interval for quotes
DEFAULT_QUOTE_INTERVAL = 60  # seconds


class QuoteCollector:
    """
    Periodically polls Deriv proposal endpoint for Touch contract pricing.
    Stores quotes for EV analysis and backtesting with real market pricing.

    IMPORTANT: This only requests price quotes. It NEVER purchases contracts.
    """

    def __init__(
        self,
        client: DerivWSClient,
        symbol: str,
        barrier: str,
        duration: int = 9,
        duration_unit: str = "m",
        barrier_direction: str = "upper",
        amount: float = 10.0,
        interval: float = DEFAULT_QUOTE_INTERVAL,
    ):
        self.client = client
        self.symbol = symbol
        self.barrier = barrier
        self.duration = duration
        self.duration_unit = duration_unit
        self.barrier_direction = barrier_direction
        self.amount = amount
        self.interval = interval
        self._running = False
        self._quote_count = 0
        self._error_count = 0

    async def start(self):
        """Start periodic quote collection."""
        self._running = True
        logger.info("quote_collector_started",
                     symbol=self.symbol,
                     barrier=self.barrier,
                     direction=self.barrier_direction,
                     interval=self.interval)

        while self._running:
            try:
                await self._fetch_and_store_quote()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.error("quote_collector_error", error=str(e))
                await asyncio.sleep(min(self.interval, 30))

    async def stop(self):
        self._running = False
        logger.info("quote_collector_stopped",
                     quotes_collected=self._quote_count,
                     errors=self._error_count)

    async def _fetch_and_store_quote(self):
        """Fetch a single quote and store it."""
        response = await self.client.get_proposal(
            symbol=self.symbol,
            contract_type=CONTRACT_ONETOUCH,
            duration=self.duration,
            duration_unit=self.duration_unit,
            barrier=self.barrier,
            amount=self.amount,
            basis="payout",
        )

        proposal = response.get("proposal", {})
        if not proposal:
            logger.warning("quote_collector_empty_proposal", response=response)
            return

        ask_price = float(proposal.get("ask_price", 0))
        payout = float(proposal.get("payout", 0))
        spot = proposal.get("spot")
        barrier_resolved = proposal.get("barrier")

        # Calculate derived values
        net_profit = payout - ask_price if payout and ask_price else None
        net_profit_pct = (net_profit / ask_price * 100) if net_profit and ask_price else None
        breakeven_prob = (ask_price / payout) if payout else None

        quote_epoch = proposal.get("date_start") or proposal.get("spot_time")
        quote_time = (
            datetime.fromtimestamp(quote_epoch, tz=timezone.utc) if quote_epoch else None
        )

        try:
            async with async_session() as session:
                stmt = pg_insert(Quote).values(
                    symbol=self.symbol,
                    contract_type=CONTRACT_ONETOUCH,
                    duration_value=self.duration,
                    duration_unit=self.duration_unit,
                    barrier_input=self.barrier,
                    barrier_direction=self.barrier_direction,
                    currency="USD",
                    ask_price=ask_price,
                    payout=payout,
                    basis="payout",
                    amount_requested=self.amount,
                    net_profit=net_profit,
                    net_profit_pct=net_profit_pct,
                    breakeven_prob=breakeven_prob,
                    spot=float(spot) if spot else None,
                    spot_time=proposal.get("spot_time"),
                    barrier_resolved=float(barrier_resolved) if barrier_resolved else None,
                    quote_epoch=quote_epoch,
                    quote_time=quote_time,
                    proposal_id=proposal.get("id"),
                    raw_response=json.dumps(response),
                )
                await session.execute(stmt)
                await session.commit()

            self._quote_count += 1
            logger.debug("quote_collected",
                          ask=ask_price, payout=payout,
                          breakeven=breakeven_prob,
                          spot=spot,
                          barrier_resolved=barrier_resolved)

        except Exception as e:
            logger.error("quote_store_error", error=str(e))

    async def fetch_single_quote(self) -> Optional[dict]:
        """Fetch a single quote without storing — for signal generation."""
        try:
            response = await self.client.get_proposal(
                symbol=self.symbol,
                contract_type=CONTRACT_ONETOUCH,
                duration=self.duration,
                duration_unit=self.duration_unit,
                barrier=self.barrier,
                amount=self.amount,
                basis="payout",
            )
            proposal = response.get("proposal", {})
            if not proposal:
                return None

            ask_price = float(proposal.get("ask_price", 0))
            payout = float(proposal.get("payout", 0))

            return {
                "ask_price": ask_price,
                "payout": payout,
                "spot": float(proposal.get("spot", 0)),
                "barrier_resolved": proposal.get("barrier"),
                "net_profit": payout - ask_price,
                "net_profit_pct": ((payout - ask_price) / ask_price * 100) if ask_price else 0,
                "breakeven_prob": ask_price / payout if payout else 1.0,
                "proposal_id": proposal.get("id"),
                "longcode": proposal.get("longcode"),
                "quote_epoch": proposal.get("spot_time"),
            }
        except Exception as e:
            logger.error("quote_fetch_single_error", error=str(e))
            return None

    @property
    def stats(self) -> dict:
        return {
            "symbol": self.symbol,
            "barrier": self.barrier,
            "direction": self.barrier_direction,
            "running": self._running,
            "quotes_collected": self._quote_count,
            "errors": self._error_count,
        }
