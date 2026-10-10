"""In-memory broker used by execution tests. It is not market evidence."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.digitmatch.errors import PurchaseNotSent, PurchaseOutcomeUnknown, PurchaseRejected


@dataclass
class FakeBroker:
    demo: bool = True
    disconnect_before_buy: bool = False
    lose_response_after_accept: bool = False
    reject_buy: bool = False
    balance: float = 10000.0
    currency: str = "USD"
    loginid: str = "VRTC100"
    symbol: str = "R_100"
    display_name: str = "Volatility 100 Index"
    contract_available: bool = True
    min_stake: float = 0.35
    spot_epoch: int = 1_000
    quote: str = "123.40"
    pip_size: int = 2
    ask: float = 1.0
    payout: float = 8.5
    scopes: list = field(default_factory=lambda: ["read", "trade"])
    connected: bool = True
    buys: list = field(default_factory=list)
    statement: list = field(default_factory=list)
    settled: dict = field(default_factory=dict)

    async def authorize_payload(self) -> dict:
        return {
            "loginid": self.loginid,
            "is_virtual": 1 if self.demo else 0,
            "currency": self.currency,
            "balance": self.balance,
            "landing_company_name": "virtual" if self.demo else "svg",
            "scopes": list(self.scopes),
            "account_list": [
                {
                    "loginid": self.loginid,
                    "is_virtual": 1 if self.demo else 0,
                    "currency": self.currency,
                }
            ],
        }

    async def active_symbols(self) -> list[dict]:
        return [{"symbol": self.symbol, "display_name": self.display_name, "pip": 0.01}]

    async def contracts_for(self, symbol: str) -> dict:
        if not self.contract_available or symbol != self.symbol:
            return {"available": []}
        return {
            "available": [
                {
                    "contract_type": "DIGITMATCH",
                    "expiry_type": "tick",
                    "min_contract_duration": "5t",
                    "max_contract_duration": "10t",
                    "min_stake": self.min_stake,
                    "contract_display": "Matches",
                }
            ]
        }

    async def proposal(self, digit: int, stake: float, currency: str) -> dict:
        return {
            "id": f"prop-{digit}",
            "ask_price": self.ask,
            "total_payout": self.payout,
            "spot_time": self.spot_epoch,
            "spot": self.quote,
            "longcode": f"Digit matches {digit}",
        }

    async def buy(self, proposal_id: str, price: float) -> dict:
        if self.disconnect_before_buy or not self.connected:
            raise PurchaseNotSent("socket closed before the buy message was written")
        contract_id = str(9000 + len(self.buys) + 1)
        record = {
            "proposal_id": proposal_id,
            "price": price,
            "contract_id": contract_id,
        }
        self.buys.append(record)
        self.statement.append(
            {
                "action_type": "buy",
                "amount": price,
                "contract_id": contract_id,
                "transaction_id": f"tx-{contract_id}",
                "transaction_time": self.spot_epoch,
                "longcode": proposal_id,
            }
        )
        if self.lose_response_after_accept:
            raise PurchaseOutcomeUnknown("response lost after the broker accepted the purchase")
        if self.reject_buy:
            self.buys.pop()
            self.statement.pop()
            raise PurchaseRejected("buy rejected by the broker")
        return {
            "contract_id": contract_id,
            "buy_price": price,
            "total_payout": self.payout,
            "purchase_time": self.spot_epoch,
            "transaction_id": f"tx-{contract_id}",
            "longcode": proposal_id,
            "balance_after": self.balance - price,
        }

    async def open_contract(self, contract_id: str) -> dict:
        override = self.settled.get(contract_id)
        if override:
            return override
        return {
            "contract_id": contract_id,
            "status": "open",
            "is_expired": 0,
            "is_sold": 0,
            "profit": None,
            "current_spot": self.quote,
        }

    async def statement_rows(self) -> list[dict]:
        return list(self.statement)

    async def profit_table_rows(self) -> list[dict]:
        return []

    async def balance_info(self) -> dict:
        return {"balance": self.balance, "currency": self.currency}
