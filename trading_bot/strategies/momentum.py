"""Momentum / mean-reversion on crypto interval markets using recent price
action, per requirement #3. Reads the rolling implied-probability history
this engine has been building up cycle over cycle (see exchanges/price_history.py)
- there is no signal until a few poll cycles have accumulated history, which
is expected and logged plainly rather than treated as an error.
"""
from __future__ import annotations

from ..config import RiskConfig
from ..exchanges.price_history import PriceHistory
from ..models import AccountState, MarketSnapshot, NoTradeDecision, OrderAction, Side, TradeSignal
from .base import Strategy

MIN_HISTORY_POINTS = 5
CONTINUATION_BAND = (0.03, 0.15)  # moderate move -> bet it continues
REVERSION_THRESHOLD = 0.15  # sharp move -> bet it reverts
DEFAULT_TRADE_DOLLARS = 75.0


class CryptoMomentumStrategy(Strategy):
    name = "crypto_momentum"

    def __init__(self, history: PriceHistory):
        self.history = history

    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = False,
    ) -> TradeSignal | NoTradeDecision:
        points = self.history.recent(market.ticker, n=MIN_HISTORY_POINTS)
        if len(points) < MIN_HISTORY_POINTS:
            return NoTradeDecision(
                self.name,
                market.ticker,
                f"only {len(points)}/{MIN_HISTORY_POINTS} history points collected so far, "
                "needs more poll cycles before it can evaluate momentum",
            )

        first_prob = points[0][1]
        last_prob = points[-1][1]
        delta = last_prob - first_prob
        abs_delta = abs(delta)

        reasoning_base = (
            f"implied_yes_prob moved from {first_prob:.3f} to {last_prob:.3f} "
            f"over last {len(points)} cycles (delta={delta:+.3f})"
        )

        if abs_delta < CONTINUATION_BAND[0]:
            return NoTradeDecision(self.name, market.ticker, f"move too small to act on. {reasoning_base}")

        max_dollars = risk.day_one_max_position_abs if day_one_mode else DEFAULT_TRADE_DOLLARS

        if CONTINUATION_BAND[0] <= abs_delta <= CONTINUATION_BAND[1]:
            mode = "continuation"
            side = Side.YES if delta > 0 else Side.NO
        elif abs_delta > REVERSION_THRESHOLD:
            mode = "mean-reversion"
            side = Side.NO if delta > 0 else Side.YES
        else:
            return NoTradeDecision(self.name, market.ticker, f"move in ambiguous zone between continuation and reversion bands. {reasoning_base}")

        price_cents = market.yes_ask if side == Side.YES else market.no_ask
        if price_cents <= 0 or price_cents >= 100:
            return NoTradeDecision(self.name, market.ticker, f"no valid ask price on {side.value} side. {reasoning_base}")

        contracts = max(1, int(max_dollars * 100 / price_cents))
        confidence = min(0.85, abs_delta * 2)

        return TradeSignal(
            strategy=self.name,
            ticker=market.ticker,
            action=OrderAction.BUY,
            side=side,
            size_contracts=contracts,
            limit_price_cents=price_cents,
            confidence=confidence,
            reasoning=f"{mode} signal: {reasoning_base}, betting {side.value}",
            edge=delta,
        )
