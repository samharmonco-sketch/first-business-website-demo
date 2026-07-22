"""Exchange adapter interface.

Every platform integration (Kalshi, and later Polymarket or others)
implements this interface. The strategy/risk/execution layers only ever
talk to this interface, so adding a second platform never requires
touching the engine or any strategy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AccountState, Market, OrderRequest, OrderResult


class ExchangeAdapter(ABC):
    """Read-only market data + trading operations for one exchange."""

    name: str = "base"

    @abstractmethod
    def get_markets(self, series_tickers: list[str], limit: int = 50) -> list[Market]:
        """Return currently open markets for the given series tickers."""

    @abstractmethod
    def get_account_state(self) -> AccountState:
        """Return current balance, positions, and open orders."""

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order. Must be a no-op / simulated in paper mode."""

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> bool:
        """Cancel a resting order. Returns True if canceled."""

    def flatten_all_positions(self, account_state: AccountState) -> list[OrderResult]:
        """Best-effort market-sell everything. Used by the kill switch's
        optional --flatten flag. Default implementation issues a sell
        OrderRequest per position at the current best bid; adapters may
        override for exchange-specific flattening semantics."""
        raise NotImplementedError
