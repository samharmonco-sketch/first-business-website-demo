from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.models import Market, SignalAction
from tradingbot.sports.data_sources import SportsDataProvider, SportsEvent, SportsEventData
from tradingbot.strategies.sports_ai import SportsAIStrategy


class FakeMarketDataAdapter(ExchangeAdapter):
    name = "fake"

    def __init__(self, markets_by_ticker):
        self._markets = markets_by_ticker

    def get_markets(self, series_tickers, limit=50):
        return list(self._markets.values())

    def get_market(self, ticker):
        return self._markets.get(ticker)

    def get_account_state(self):
        raise NotImplementedError

    def place_order(self, order):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError


class FakeDataProvider(SportsDataProvider):
    def __init__(self, events, event_data_by_id):
        self._events = events
        self._data = event_data_by_id

    def get_upcoming_events(self, series_tickers, max_events):
        return self._events[:max_events]

    def get_event_data(self, event):
        return self._data[event.event_id]


class FakeAnalyzer:
    """Stand-in for SportsAnalyzer that returns a fixed estimate without
    calling the real Anthropic API."""
    def __init__(self, probability, confidence):
        self.probability = probability
        self.confidence = confidence

    def analyze(self, data):
        from tradingbot.sports.analyzer import AIAnalysisResult
        return AIAnalysisResult(
            probability=self.probability, confidence=self.confidence,
            reasoning="fake reasoning for test", raw_response="{}", prompt="fake prompt",
        )


def test_sports_ai_uses_kalshi_market_price_not_sportsbook_odds():
    market = Market(ticker="KXNBA-LAL-BOS", series_ticker="KXNBA", title="Will the Lakers win?",
                     yes_bid=0.40, yes_ask=0.42, no_bid=0.58, no_ask=0.60, volume=50, close_ts=9999999999)
    event = SportsEvent(event_id="evt1", league="NBA", home_team="Los Angeles Lakers",
                         away_team="Boston Celtics", start_time_iso="2026-01-01T00:00:00Z",
                         market_ticker="KXNBA-LAL-BOS", outcome_description="Lakers win")
    # Sportsbook odds imply a totally different probability than what should
    # drive the trade -- the strategy must use the Kalshi market price (0.41
    # mid), not this, for its edge calculation.
    event_data = SportsEventData(
        event=event, sportsbook_odds={"devigged_yes_side_probability": 0.20}, data_confidence=1.0,
    )
    provider = FakeDataProvider([event], {"evt1": event_data})
    adapter = FakeMarketDataAdapter({"KXNBA-LAL-BOS": market})

    strategy = SportsAIStrategy({}, data_provider=provider, market_data_adapter=adapter)
    strategy._analyzer = FakeAnalyzer(probability=0.70, confidence=0.8)

    signals = strategy.evaluate_events(["KXNBA"])
    assert len(signals) == 1
    signal = signals[0]

    assert signal.action == SignalAction.BUY_YES
    # edge should be computed against Kalshi's yes_mid (0.41), not the
    # sportsbook devigged probability (0.20).
    expected_kalshi_mid = (0.40 + 0.42) / 2
    assert signal.inputs["kalshi_implied_probability"] == expected_kalshi_mid
    assert abs(signal.edge - (0.70 - expected_kalshi_mid)) < 1e-9
    assert signal.inputs["yes_ask"] == 0.42  # needed for correct order pricing


def test_sports_ai_holds_when_no_kalshi_market_match():
    event = SportsEvent(event_id="evt1", league="NBA", home_team="A", away_team="B",
                         start_time_iso="2026-01-01T00:00:00Z", market_ticker="KXNBA-MISSING",
                         outcome_description="A wins")
    event_data = SportsEventData(event=event, data_confidence=1.0)
    provider = FakeDataProvider([event], {"evt1": event_data})
    adapter = FakeMarketDataAdapter({})  # no markets available

    strategy = SportsAIStrategy({}, data_provider=provider, market_data_adapter=adapter)
    signals = strategy.evaluate_events(["KXNBA"])

    assert len(signals) == 1
    assert signals[0].action == SignalAction.HOLD
    assert "could not fetch Kalshi market" in signals[0].reasoning


def test_sports_ai_holds_below_min_data_confidence():
    market = Market(ticker="KXNBA-LAL-BOS", series_ticker="KXNBA", title="Will the Lakers win?",
                     yes_bid=0.40, yes_ask=0.42, no_bid=0.58, no_ask=0.60, volume=50, close_ts=9999999999)
    event = SportsEvent(event_id="evt1", league="NBA", home_team="Los Angeles Lakers",
                         away_team="Boston Celtics", start_time_iso="2026-01-01T00:00:00Z",
                         market_ticker="KXNBA-LAL-BOS", outcome_description="Lakers win")
    event_data = SportsEventData(event=event, data_confidence=0.1)  # below default min 0.4
    provider = FakeDataProvider([event], {"evt1": event_data})
    adapter = FakeMarketDataAdapter({"KXNBA-LAL-BOS": market})

    strategy = SportsAIStrategy({"min_data_confidence": 0.4}, data_provider=provider, market_data_adapter=adapter)
    signals = strategy.evaluate_events(["KXNBA"])

    assert len(signals) == 1
    assert signals[0].action == SignalAction.HOLD
    assert "data confidence" in signals[0].reasoning
