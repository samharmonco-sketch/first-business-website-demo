"""Pluggable strategy interface. Every strategy takes the same three inputs
(market snapshot, account state, risk config) and returns either a TradeSignal
or a NoTradeDecision - never places an order itself. Adding a new strategy
means writing one class here and registering it in engine/execution_engine.py's
STRATEGIES list; nothing else in the engine needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import RiskConfig
from ..models import AccountState, MarketSnapshot, NoTradeDecision, TradeSignal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = False,
    ) -> TradeSignal | NoTradeDecision:
        """Look at one market and decide: trade signal, or a logged no-trade reason."""
