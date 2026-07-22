"""Common interface every exchange adapter must implement. The execution engine
and strategies only ever talk to this interface, so a second platform (e.g.
Polymarket) can be added later without touching engine or strategy code."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import MarketCategory, MarketSnapshot, Order, OrderAction, Side


class ExchangeAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def get_markets(self, category: MarketCategory | None = None) -> list[MarketSnapshot]:
        """Return current open markets, optionally filtered by category."""

    @abstractmethod
    def get_orderbook(self, ticker: str) -> MarketSnapshot:
        """Return a fresh snapshot (best bid/ask) for one market."""

    @abstractmethod
    def place_order(
        self, ticker: str, action: OrderAction, side: Side, contracts: int, price_cents: float
    ) -> Order:
        """Place a real order. Never called directly by strategies — only by the
        execution engine, and only when config.mode == 'live'."""

    @abstractmethod
    def get_positions(self) -> list:
        """Return real exchange positions (used only in live mode for reconciliation)."""
