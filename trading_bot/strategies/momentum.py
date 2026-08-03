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
MIN_ENTRY_PRICE_CENTS = 20  # below this, Kalshi crypto interval strikes are effectively
# worthless deep-OTM contracts with no real buyer-side liquidity - the ask lingers but
# the bid is already at or near 0. Every one of these entered near-certain and stopped
# out at -100% within a cycle or two (see the SHIBAD/DOGED/BTC-at-1c losses that drove
# the account from $1,000 to under $85). Requiring a real, non-zero bid on the entry
# side catches the same failure mode directly: a lingering ask with no bid behind it
# is exactly the "about to expire worthless" signature.


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

        if day_one_mode:
            max_dollars = risk.day_one_max_position_abs
        elif risk.validation_mode_enabled:
            max_dollars = risk.validation_mode_trade_dollars
        else:
            max_dollars = DEFAULT_TRADE_DOLLARS

        if CONTINUATION_BAND[0] <= abs_delta <= CONTINUATION_BAND[1]:
            mode = "continuation"
            side = Side.YES if delta > 0 else Side.NO
        elif abs_delta > REVERSION_THRESHOLD:
            mode = "mean-reversion"
            side = Side.NO if delta > 0 else Side.YES
        else:
            return NoTradeDecision(self.name, market.ticker, f"move in ambiguous zone between continuation and reversion bands. {reasoning_base}")

        price_cents = market.yes_ask if side == Side.YES else market.no_ask
        bid_cents = market.yes_bid if side == Side.YES else market.no_bid
        if price_cents <= 0 or price_cents >= 100:
            return NoTradeDecision(self.name, market.ticker, f"no valid ask price on {side.value} side. {reasoning_base}")
        if price_cents < MIN_ENTRY_PRICE_CENTS:
            return NoTradeDecision(
                self.name, market.ticker,
                f"ask {price_cents:.0f}c below min entry floor {MIN_ENTRY_PRICE_CENTS}c - "
                f"too deep OTM / illiquid to trust. {reasoning_base}",
            )
        if bid_cents <= 0:
            return NoTradeDecision(
                self.name, market.ticker,
                f"no bid on {side.value} side (ask {price_cents:.0f}c but bid={bid_cents:.0f}c) - "
                f"no real two-sided market, likely about to expire worthless. {reasoning_base}",
            )

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
