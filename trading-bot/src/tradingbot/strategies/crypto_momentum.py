"""Momentum / mean-reversion strategy for crypto interval markets.

Tracks a rolling window of each market's mid-price (the yes_mid, which is
the market's own implied probability of the "up"/threshold outcome). Two
signals, evaluated per market per cycle:

  Momentum:      if price has moved consistently in one direction over the
                  lookback window by more than `momentum_threshold`, bet
                  that it continues (buy in the direction of the move).
  Mean-reversion: if the current price is more than `mean_reversion_zscore`
                  standard deviations from the rolling mean, bet on reversion
                  toward the mean.

These two signals are mutually exclusive per evaluation (momentum is
checked first since interval markets close soon and short windows tend to
trend into expiry more than mean-revert). This is intentionally simple --
it is a starter strategy, not a research-grade model.
"""
from __future__ import annotations

import statistics
from collections import defaultdict, deque

from ..models import AccountState, Market, Signal, SignalAction
from .base import Strategy


class CryptoMomentumStrategy(Strategy):
    name = "crypto_momentum"

    def __init__(self, params: dict):
        super().__init__(params)
        self.lookback = int(params.get("lookback_ticks", 20))
        self.momentum_threshold = float(params.get("momentum_threshold", 0.015))
        self.mean_reversion_zscore = float(params.get("mean_reversion_zscore", 2.0))
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.lookback))

    def evaluate(self, market: Market, account_state: AccountState) -> Signal:
        history = self._history[market.ticker]
        price = market.yes_mid
        history.append(price)

        base_inputs = {
            "price": price, "history_len": len(history), "yes_bid": market.yes_bid,
            "yes_ask": market.yes_ask, "no_ask": market.no_ask,
        }

        if len(history) < max(5, self.lookback // 2):
            return Signal(
                strategy_name=self.name, ticker=market.ticker, action=SignalAction.HOLD,
                size_usd=0.0, confidence=0.0, edge=0.0,
                reasoning=f"insufficient price history ({len(history)}/{self.lookback} ticks) to evaluate momentum/reversion",
                inputs=base_inputs,
            )

        prices = list(history)
        start_price = prices[0]
        pct_change = (price - start_price) / start_price if start_price > 0 else 0.0

        mean = statistics.fmean(prices)
        stdev = statistics.pstdev(prices) if len(prices) > 1 else 0.0
        zscore = (price - mean) / stdev if stdev > 1e-9 else 0.0

        base_inputs.update({"pct_change_over_window": pct_change, "rolling_mean": mean, "rolling_stdev": stdev, "zscore": zscore})

        # Momentum: price trending up -> buy YES; trending down -> buy NO.
        if abs(pct_change) >= self.momentum_threshold:
            confidence = min(0.9, 0.5 + abs(pct_change) * 5)
            edge = abs(pct_change)
            if pct_change > 0:
                action = SignalAction.BUY_YES
                reasoning = (f"momentum: yes_mid rose {pct_change:.2%} over last {len(prices)} ticks "
                             f"(threshold {self.momentum_threshold:.2%}); betting continuation upward")
            else:
                action = SignalAction.BUY_NO
                reasoning = (f"momentum: yes_mid fell {pct_change:.2%} over last {len(prices)} ticks "
                             f"(threshold {self.momentum_threshold:.2%}); betting continuation downward")
            return Signal(
                strategy_name=self.name, ticker=market.ticker, action=action,
                size_usd=self._size_for_confidence(confidence), confidence=confidence, edge=edge,
                reasoning=reasoning, inputs=base_inputs,
            )

        # Mean reversion: price far from rolling mean -> bet it reverts.
        if abs(zscore) >= self.mean_reversion_zscore:
            confidence = min(0.85, 0.5 + (abs(zscore) - self.mean_reversion_zscore) * 0.1)
            edge = min(0.5, abs(zscore) * 0.02)
            if zscore > 0:
                # price above mean -> expect reversion down -> buy NO
                action = SignalAction.BUY_NO
                reasoning = (f"mean-reversion: yes_mid z-score {zscore:.2f} above rolling mean "
                             f"{mean:.3f} (threshold {self.mean_reversion_zscore:.1f}); betting reversion down")
            else:
                action = SignalAction.BUY_YES
                reasoning = (f"mean-reversion: yes_mid z-score {zscore:.2f} below rolling mean "
                             f"{mean:.3f} (threshold {self.mean_reversion_zscore:.1f}); betting reversion up")
            return Signal(
                strategy_name=self.name, ticker=market.ticker, action=action,
                size_usd=self._size_for_confidence(confidence), confidence=confidence, edge=edge,
                reasoning=reasoning, inputs=base_inputs,
            )

        return Signal(
            strategy_name=self.name, ticker=market.ticker, action=SignalAction.HOLD,
            size_usd=0.0, confidence=0.0, edge=0.0,
            reasoning=(f"no signal: pct_change {pct_change:.2%} below momentum threshold "
                       f"{self.momentum_threshold:.2%} and zscore {zscore:.2f} below reversion "
                       f"threshold {self.mean_reversion_zscore:.1f}"),
            inputs=base_inputs,
        )

    def _size_for_confidence(self, confidence: float) -> float:
        # Base size scales with confidence; risk manager applies the hard caps.
        return round(10.0 + 40.0 * confidence, 2)
