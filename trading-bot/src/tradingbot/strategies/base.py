"""Strategy plugin interface.

To add a new strategy: subclass Strategy, implement evaluate(), and add
its name to config.yaml's strategies.enabled list plus the registry map in
registry.py. The execution engine never needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AccountState, Market, Signal


class Strategy(ABC):
    name: str = "base"

    def __init__(self, params: dict):
        self.params = params

    @abstractmethod
    def evaluate(self, market: Market, account_state: AccountState) -> Signal:
        """Given one market and current account state, return a Signal.

        Must always return a Signal (use SignalAction.HOLD for no-trade)
        so that every evaluation gets logged, not just the ones that trade.
        """
