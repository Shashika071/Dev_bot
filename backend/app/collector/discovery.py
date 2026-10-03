"""
Instrument and contract discovery via Deriv API.
Fetches active_symbols and contracts_for to determine available instruments and contracts.
"""

import json
import structlog
from typing import Optional

from app.deriv.client import DerivWSClient
from app.deriv import SYMBOL_R_100, SYMBOL_R_100_1S, CONTRACT_ONETOUCH

logger = structlog.get_logger(__name__)


class ContractDiscovery:
    """
    Discovers available instruments and contract types from Deriv API.
    Used by the setup screen to confirm correct symbol and contract parameters.
    """

    def __init__(self, client: DerivWSClient):
        self.client = client
        self._symbols_cache: Optional[dict] = None
        self._contracts_cache: dict[str, dict] = {}

    async def discover_symbols(self) -> list[dict]:
        """
        Fetch all active symbols and filter for Volatility 100 variants.
        Returns list of symbol info dicts.
        """
        response = await self.client.get_active_symbols()
        all_symbols = response.get("active_symbols", [])

        vol100_symbols = []
        for sym in all_symbols:
            symbol = sym.get("symbol", "")
            if symbol in (SYMBOL_R_100, SYMBOL_R_100_1S):
                vol100_symbols.append({
                    "symbol": symbol,
                    "display_name": sym.get("display_name", ""),
                    "market": sym.get("market", ""),
                    "market_display_name": sym.get("market_display_name", ""),
                    "submarket": sym.get("submarket", ""),
                    "pip": sym.get("pip", 0),
                    "is_trading_suspended": sym.get("is_trading_suspended", 0),
                    "exchange_is_open": sym.get("exchange_is_open", 0),
                })

        self._symbols_cache = {s["symbol"]: s for s in vol100_symbols}
        logger.info("discovery_symbols_found", count=len(vol100_symbols),
                     symbols=[s["symbol"] for s in vol100_symbols])
        return vol100_symbols

    async def discover_contracts(self, symbol: str) -> dict:
        """
        Fetch contracts_for a symbol and extract Touch-related contract info.
        Returns detailed contract availability information.
        """
        response = await self.client.get_contracts_for(symbol)
        contracts = response.get("contracts_for", {})
        available = contracts.get("available", [])

        touch_contracts = []
        for contract in available:
            ctype = contract.get("contract_type", "")
            if ctype in (CONTRACT_ONETOUCH, "NOTOUCH"):
                touch_contracts.append({
                    "contract_type": ctype,
                    "contract_category": contract.get("contract_category", ""),
                    "contract_category_display": contract.get("contract_category_display", ""),
                    "contract_display": contract.get("contract_display", ""),
                    "min_contract_duration": contract.get("min_contract_duration", ""),
                    "max_contract_duration": contract.get("max_contract_duration", ""),
                    "barrier_category": contract.get("barrier_category", ""),
                    "barriers": contract.get("barriers", 0),
                    "expiry_type": contract.get("expiry_type", ""),
                    "sentiment": contract.get("sentiment", ""),
                    "start_type": contract.get("start_type", ""),
                })

        result = {
            "symbol": symbol,
            "feed_license": contracts.get("feed_license", ""),
            "spot": contracts.get("spot", None),
            "spot_time": contracts.get("spot_time", None),
            "touch_contracts": touch_contracts,
            "has_onetouch": any(c["contract_type"] == CONTRACT_ONETOUCH for c in touch_contracts),
            "raw_response": json.dumps(contracts),
        }

        self._contracts_cache[symbol] = result
        logger.info("discovery_contracts_found",
                     symbol=symbol,
                     touch_count=len(touch_contracts),
                     has_onetouch=result["has_onetouch"])
        return result

    async def get_sample_quote(
        self,
        symbol: str,
        barrier: str,
        duration: int = 9,
        duration_unit: str = "m",
        amount: float = 10.0,
    ) -> dict:
        """
        Get a sample Touch quote to confirm barrier interpretation.
        This does NOT purchase — only requests pricing.
        """
        try:
            response = await self.client.get_proposal(
                symbol=symbol,
                contract_type=CONTRACT_ONETOUCH,
                duration=duration,
                duration_unit=duration_unit,
                barrier=barrier,
                amount=amount,
                basis="payout",
            )

            proposal = response.get("proposal", {})
            return {
                "success": True,
                "ask_price": proposal.get("ask_price"),
                "payout": proposal.get("payout"),
                "spot": proposal.get("spot"),
                "spot_time": proposal.get("spot_time"),
                "barrier": proposal.get("barrier"),  # Resolved absolute barrier
                "date_start": proposal.get("date_start"),
                "date_expiry": proposal.get("date_expiry"),
                "longcode": proposal.get("longcode"),  # Human-readable description
                "proposal_id": proposal.get("id"),
                "raw_response": json.dumps(response),
            }
        except Exception as e:
            logger.error("discovery_quote_failed", symbol=symbol, barrier=barrier, error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
