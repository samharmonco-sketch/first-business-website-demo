"""Trivial "always small buy" strategy.

Exists solely to prove the execution loop works end-to-end (poll -> evaluate
-> risk check -> place order -> log) before any real strategy logic is
trusted. Keep disabled (strategies.dummy_buy.enabled: false) outside of
smoke testing -- it has no real edge model.
"""
from __future__ import annotations

from ..models import AccountState, Market, Signal, SignalAction
from .base import Strategy


class DummyAlwaysSmallBuyStrategy(Strategy):
    name = "dummy_buy"

    def evaluate(self, market: Market, account_state: AccountState) -> Signal:
        size_usd = float(self.params.get("size_usd", 1.0))
        return Signal(
            strategy_name=self.name,
            ticker=market.ticker,
            action=SignalAction.BUY_YES,
            size_usd=size_usd,
            confidence=1.0,
            edge=1.0,
            reasoning="dummy_buy strategy: unconditionally signals a small YES buy to smoke-test the loop.",
            inputs={"yes_ask": market.yes_ask},
        )
