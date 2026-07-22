"""Day-one permissive mode, per requirement #2: on first launch the bot should
produce a visible result immediately rather than staring at an empty log for
an hour. This wraps the real steady-state strategies and re-runs them with
day_one_mode=True, which relaxes each strategy's own edge threshold and caps
size to risk.day_one_max_position_abs (both enforced inside the wrapped
strategies and again by the risk manager). It is a thin pass-through, not a
separate pricing model, so "day one" and "steady state" never disagree about
what a good trade looks like - only about how permissive the threshold is.
"""
from __future__ import annotations

from ..config import RiskConfig
from ..models import AccountState, MarketSnapshot, NoTradeDecision, TradeSignal
from .base import Strategy


class DayOneStrategy(Strategy):
    name = "day_one"

    def __init__(self, wrapped: list[Strategy]):
        self.wrapped = wrapped

    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = True,
    ) -> TradeSignal | NoTradeDecision:
        reasons = []
        for strategy in self.wrapped:
            result = strategy.evaluate(market, account, risk, day_one_mode=True)
            if isinstance(result, TradeSignal):
                result.strategy = f"day_one/{strategy.name}"
                result.extra["day_one_mode"] = True
                return result
            reasons.append(f"{strategy.name}: {result.reasoning}")
        return NoTradeDecision(self.name, market.ticker, " | ".join(reasons))
