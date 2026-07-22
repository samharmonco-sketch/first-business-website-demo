"""AI sports analysis strategy.

For each configured sports market, fetches event data (stats/injuries/odds/
recent form) via a pluggable SportsDataProvider, asks Claude for a
probability + confidence estimate, and compares that estimate to the
market's own implied probability to find edge.

Note: this strategy operates on *events*, not raw Market objects the way
crypto strategies do, since it needs richer context than one market's
price. The execution engine calls `evaluate_events()` for this strategy
type in addition to the standard per-market `evaluate()` (which is a
thin no-op wrapper so the interface stays satisfied for markets with no
matching sports event).
"""
from __future__ import annotations

from pathlib import Path

from ..adapters.base import ExchangeAdapter
from ..config import AnthropicConfig
from ..models import AccountState, Market, Signal, SignalAction
from ..sports.analyzer import SportsAnalyzer
from ..sports.data_sources import SportsDataProvider, StubSportsDataProvider
from .base import Strategy


class SportsAIStrategy(Strategy):
    name = "sports_ai"

    def __init__(self, params: dict, anthropic_config: AnthropicConfig | None = None,
                 data_provider: SportsDataProvider | None = None, log_dir: Path | None = None,
                 market_data_adapter: ExchangeAdapter | None = None):
        super().__init__(params)
        self.max_events_per_cycle = int(params.get("max_events_per_cycle", 5))
        self.min_data_confidence = float(params.get("min_data_confidence", 0.4))
        self.data_provider = data_provider or StubSportsDataProvider()
        self.market_data_adapter = market_data_adapter
        self._anthropic_config = anthropic_config
        self._analyzer = None
        self._log_dir = log_dir or Path("logs")

    def _get_analyzer(self) -> SportsAnalyzer:
        if self._analyzer is None:
            if self._anthropic_config is None:
                raise RuntimeError("sports_ai strategy requires an AnthropicConfig")
            self._analyzer = SportsAnalyzer(self._anthropic_config, self._log_dir / "sports_audit.jsonl")
        return self._analyzer

    def evaluate(self, market: Market, account_state: AccountState) -> Signal:
        # Standard per-market interface: sports_ai doesn't act on generic
        # Market objects directly (it needs event context), so this is a
        # deliberate HOLD. The engine calls evaluate_events() separately
        # for sports series tickers.
        return Signal(
            strategy_name=self.name, ticker=market.ticker, action=SignalAction.HOLD,
            size_usd=0.0, confidence=0.0, edge=0.0,
            reasoning="sports_ai evaluates via evaluate_events(), not per-market evaluate()",
        )

    def evaluate_events(self, series_tickers: list[str]) -> list[Signal]:
        events = self.data_provider.get_upcoming_events(series_tickers, self.max_events_per_cycle)
        if not events:
            return [Signal(
                strategy_name=self.name, ticker="(none)", action=SignalAction.HOLD,
                size_usd=0.0, confidence=0.0, edge=0.0,
                reasoning="no sports data provider configured or no upcoming events found; "
                          "wire a real SportsDataProvider to enable sports trading",
            )]

        signals = []
        for event in events:
            try:
                data = self.data_provider.get_event_data(event)
            except NotImplementedError as exc:
                signals.append(Signal(
                    strategy_name=self.name, ticker=event.market_ticker, action=SignalAction.HOLD,
                    size_usd=0.0, confidence=0.0, edge=0.0, reasoning=str(exc),
                ))
                continue

            if data.data_confidence < self.min_data_confidence:
                signals.append(Signal(
                    strategy_name=self.name, ticker=event.market_ticker, action=SignalAction.HOLD,
                    size_usd=0.0, confidence=0.0, edge=0.0,
                    reasoning=f"underlying data confidence {data.data_confidence:.2f} below minimum "
                              f"{self.min_data_confidence:.2f}; skipping to avoid trading on thin data",
                    inputs={"data_source_note": data.data_source_note},
                ))
                continue

            # The edge comparison MUST be against the actual Kalshi market's
            # current price, not any sportsbook odds (those are only LLM
            # input context) -- fetch the live market for this event's ticker.
            if self.market_data_adapter is None:
                signals.append(Signal(
                    strategy_name=self.name, ticker=event.market_ticker, action=SignalAction.HOLD,
                    size_usd=0.0, confidence=0.0, edge=0.0,
                    reasoning="no market_data_adapter configured; cannot fetch Kalshi market price for edge comparison",
                ))
                continue
            kalshi_market = self.market_data_adapter.get_market(event.market_ticker)
            if kalshi_market is None:
                signals.append(Signal(
                    strategy_name=self.name, ticker=event.market_ticker, action=SignalAction.HOLD,
                    size_usd=0.0, confidence=0.0, edge=0.0,
                    reasoning=f"could not fetch Kalshi market {event.market_ticker}; may be closed or ticker mismatch",
                ))
                continue

            analyzer = self._get_analyzer()
            result = analyzer.analyze(data)
            if result is None:
                signals.append(Signal(
                    strategy_name=self.name, ticker=event.market_ticker, action=SignalAction.HOLD,
                    size_usd=0.0, confidence=0.0, edge=0.0,
                    reasoning="Claude analysis failed or returned unparseable output; see sports_audit.jsonl",
                ))
                continue

            implied_prob = kalshi_market.implied_yes_probability
            edge = result.probability - implied_prob
            confidence = result.confidence
            reasoning = (f"AI estimate: P({event.outcome_description})={result.probability:.3f} "
                         f"(confidence {confidence:.2f}) vs Kalshi market implied {implied_prob:.3f} "
                         f"({event.market_ticker}) -> edge {edge:+.3f}. Claude reasoning: {result.reasoning}")

            if edge > 0:
                action = SignalAction.BUY_YES
            elif edge < 0:
                action = SignalAction.BUY_NO
            else:
                action = SignalAction.HOLD

            size_usd = round(10.0 + 40.0 * confidence, 2) if action != SignalAction.HOLD else 0.0
            signals.append(Signal(
                strategy_name=self.name, ticker=event.market_ticker, action=action,
                size_usd=size_usd, confidence=confidence, edge=abs(edge), reasoning=reasoning,
                inputs={"ai_probability": result.probability, "ai_confidence": result.confidence,
                        "kalshi_implied_probability": implied_prob, "yes_bid": kalshi_market.yes_bid,
                        "yes_ask": kalshi_market.yes_ask, "no_ask": kalshi_market.no_ask,
                        "sportsbook_odds": data.sportsbook_odds, "prompt": result.prompt[:2000]},
            ))
        return signals
