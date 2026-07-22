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
    market = make_kalshi_market("KXNBA-LAL-BOS", "Will the Lakers win?", now + 10 * 86400)  # 10 days out, past the 96h window
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Los Angeles Lakers", "away_team": "Boston Celtics",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [market])
    assert result is None


def test_match_kalshi_market_uses_city_name_not_mascot():
    """Realistic case discovered against Kalshi's real demo environment:
    titles/sub_titles name teams by city only ("Carolina"), never by
    mascot ("Panthers") -- matching must work off the full team name."""
    now = time.time()
    market = make_kalshi_market(
        "KXNFLGAME-CARARI-CAR", "Will Carolina win the Carolina vs Arizona Pro Football game?",
        now + 3 * 86400, raw={"yes_sub_title": "Carolina"},  # Kalshi's real ~72h settlement buffer
    )
    provider = make_provider([market])
    raw_event = {
        "id": "evt1", "home_team": "Carolina Panthers", "away_team": "Arizona Cardinals",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [market])
    assert result is not None
    matched_market, yes_team = result
    assert matched_market.ticker == "KXNFLGAME-CARARI-CAR"
    assert yes_team == "Carolina Panthers"


def test_match_kalshi_market_picks_closest_game_in_back_to_back_series():
    """Two teams playing consecutive games (common in MLB) each have their
    own market -- the matcher must pick the one whose close_time is
    actually closest to this event's commence_time, not just any team
    match within the (now wide) window."""
    now = time.time()
    right_game = make_kalshi_market(
        "KXMLB-KC-DET-G1", "Kansas City vs Detroit", now + 3 * 86400,
        raw={"yes_sub_title": "Kansas City"},
    )
    other_game_next_day = make_kalshi_market(
        "KXMLB-KC-DET-G2", "Kansas City vs Detroit", now + 4 * 86400,
        raw={"yes_sub_title": "Kansas City"},
    )
    provider = make_provider([right_game, other_game_next_day])
    raw_event = {
        "id": "evt1", "home_team": "Kansas City Royals", "away_team": "Detroit Tigers",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [right_game, other_game_next_day])
    assert result is not None
    matched_market, yes_team = result
    assert matched_market.ticker == "KXMLB-KC-DET-G1"
    assert yes_team == "Kansas City Royals"


def test_match_kalshi_market_home_away_variants_of_same_game_not_ambiguous():
    """Kalshi lists two markets per game (one yes-side per team), both with
    the same close_time -- that pairing must NOT be treated as ambiguous."""
    now = time.time()
    home_market = make_kalshi_market("KXNFL-CAR", "Carolina vs Arizona", now + 3 * 86400,
                                      raw={"yes_sub_title": "Carolina"})
    away_market = make_kalshi_market("KXNFL-ARI", "Carolina vs Arizona", now + 3 * 86400,
                                      raw={"yes_sub_title": "Arizona"})
    provider = make_provider([home_market, away_market])
    raw_event = {
        "id": "evt1", "home_team": "Carolina Panthers", "away_team": "Arizona Cardinals",
        "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    result = provider._match_kalshi_market(raw_event, [home_market, away_market])
    assert result is not None


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
