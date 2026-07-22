import time

from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.models import Market
from tradingbot.sports.odds_api_provider import TheOddsAPIProvider, _american_odds_to_prob


class FakeMarketDataAdapter(ExchangeAdapter):
    name = "fake"

    def __init__(self, markets):
        self._markets = markets

    def get_markets(self, series_tickers, limit=50):
        return self._markets

    def get_market(self, ticker):
        return next((m for m in self._markets if m.ticker == ticker), None)

    def get_account_state(self):
        raise NotImplementedError

    def place_order(self, order):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError


def make_provider(markets):
    return TheOddsAPIProvider(api_key="test_key", market_data_adapter=FakeMarketDataAdapter(markets))


def make_kalshi_market(ticker, title, close_ts, raw=None):
    return Market(ticker=ticker, series_ticker="KXNBA", title=title, yes_bid=0.5, yes_ask=0.52,
                  no_bid=0.48, no_ask=0.5, volume=100, close_ts=close_ts, raw=raw or {})


def test_american_odds_to_prob_favorite_and_underdog():
    assert abs(_american_odds_to_prob(-150) - 0.6) < 0.001
    assert abs(_american_odds_to_prob(130) - (100 / 230)) < 0.001


def test_match_kalshi_market_unambiguous():
    now = time.time()
    market = make_kalshi_market("KXNBA-LAL-BOS", "Will the Lakers win?", now + 3600)
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [market])
    assert result is not None
    matched_market, yes_team = result
    assert matched_market.ticker == "KXNBA-LAL-BOS"
    assert yes_team == "Los Angeles Lakers"


def test_match_kalshi_market_disambiguated_by_yes_sub_title():
    """Realistic case: the title mentions both teams ('Lakers vs Celtics'),
    which is ambiguous on its own, but Kalshi's yes_sub_title field spells
    out which team winning is the YES outcome."""
    now = time.time()
    market = make_kalshi_market(
        "KXNBA-LAL-BOS", "Lakers vs Celtics", now + 3600,
        raw={"yes_sub_title": "Lakers win"},
    )
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [market])
    assert result is not None
    matched_market, yes_team = result
    assert yes_team == "Los Angeles Lakers"


def test_match_kalshi_market_ambiguous_when_both_teams_in_title():
    now = time.time()
    market = make_kalshi_market("KXNBA-LAL-BOS", "Lakers vs Celtics winner", now + 3600)
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [market])
    assert result is None  # can't tell which side is YES -- must not guess


def test_match_kalshi_market_rejects_outside_time_window():
    now = time.time()
    market = make_kalshi_market("KXNBA-LAL-BOS", "Will the Lakers win?", now + 100000)  # far in the future
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [market])
    assert result is None


def test_get_event_data_devigs_moneylines():
    now = time.time()
    market = make_kalshi_market("KXNBA-LAL-BOS", "Will the Lakers win?", now + 3600)
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "sport_title": "NBA",
        "bookmakers": [{
            "key": "book1", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Los Angeles Lakers", "price": -150},
                {"name": "Boston Celtics", "price": 130},
            ]}],
        }],
    }
    # Directly exercise the cache + get_event_data path used by evaluate_events.
    provider._raw_by_event_id["evt1"] = raw_event
    provider._yes_team_by_event_id["evt1"] = "Los Angeles Lakers"
    from tradingbot.sports.data_sources import SportsEvent
    event = SportsEvent(event_id="evt1", league="NBA", home_team="Los Angeles Lakers",
                         away_team="Boston Celtics", start_time_iso=raw_event["commence_time"],
                         market_ticker="KXNBA-LAL-BOS", outcome_description="Los Angeles Lakers wins")
    data = provider.get_event_data(event)
    assert data.sportsbook_odds["devigged_yes_side_probability"] is not None
    assert 0.5 < data.sportsbook_odds["devigged_yes_side_probability"] < 0.65
    assert data.data_confidence == 1.0
