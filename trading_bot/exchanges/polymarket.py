"""Polymarket adapter stub. Not wired into the engine yet - this exists to prove
the ExchangeAdapter interface is genuinely pluggable (per requirement #6:
"wrap the exchange API in an adapter layer so a second platform can be added
later without rewriting the strategy engine"). Polymarket's Gamma/CLOB APIs
are public read-only for market data (no key needed), so filling this in is
mostly get_markets/get_orderbook work whenever there's a reason to add a
second venue - order placement needs a funded wallet + the py-clob-client
signing flow, which is out of scope until requested.
"""
from __future__ import annotations

from ..models import MarketCategory, MarketSnapshot, Order, OrderAction, Side
from .base import ExchangeAdapter


class PolymarketAdapter(ExchangeAdapter):
    name = "polymarket"

    def get_markets(self, category: MarketCategory | None = None) -> list[MarketSnapshot]:
        raise NotImplementedError("Polymarket adapter is a stub - not wired in yet")

    def get_orderbook(self, ticker: str) -> MarketSnapshot:
        raise NotImplementedError

    def place_order(
        self, ticker: str, action: OrderAction, side: Side, contracts: int, price_cents: float
    ) -> Order:
        raise NotImplementedError

    def get_positions(self) -> list:
        raise NotImplementedError
