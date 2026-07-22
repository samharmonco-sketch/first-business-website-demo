"""SportsDataProvider backed by TheOddsAPI (the-odds-api.com), v4.

Fetches real upcoming events + sportsbook moneyline odds, then best-effort
matches each event to a specific open Kalshi market by team name + start
time proximity. Kalshi's sports market titles/tickers were not observable
from the development environment this was built in (network access to
both Kalshi and TheOddsAPI was blocked there), so the matching heuristic
in `_match_kalshi_market` and the series->sport-key mapping should be
verified/tuned against real Kalshi market titles for the leagues you
actually trade -- see scripts/validate_sports_ai.py.

TheOddsAPI does not provide team/player stats or injury reports -- those
fields are left empty here. Wire an additional stats/injury source into
get_event_data() if you want Claude's estimate informed by more than
odds-implied probability and recent line movement.
"""
from __future__ import annotations

import datetime
import re

import requests

from ..adapters.base import ExchangeAdapter
from ..models import Market
from .data_sources import SportsDataProvider, SportsEvent, SportsEventData

BASE_URL = "https://api.the-odds-api.com/v4"

# Kalshi sports series ticker -> TheOddsAPI sport key. Confirmed against
# Kalshi's production /trade-api/v2/series catalog: KXNFLGAME/KXNBAGAME/
# KXMLBGAME/KXNHLGAME are the game-level (single-game moneyline) series --
# override/extend via config.yaml's strategies.sports_ai.series_to_sport_key.
DEFAULT_SERIES_TO_SPORT_KEY = {
    "KXNFLGAME": "americanfootball_nfl",
    "KXNBAGAME": "basketball_nba",
    "KXMLBGAME": "baseball_mlb",
    "KXNHLGAME": "icehockey_nhl",
}


def _american_odds_to_prob(odds: float) -> float:
    if odds < 0:
        return (-odds) / (-odds + 100)
    return 100 / (odds + 100)


_STOPWORDS = {"the", "of", "fc"}


def _team_tokens(team_name: str) -> set[str]:
    # Kalshi market titles/sub-titles are inconsistent about whether they
    # name a team by its city ("Carolina", "Kansas City") or its mascot
    # ("Panthers") -- e.g. real Kalshi NFL/MLB/NBA/NHL titles observed in
    # production use city names exclusively, with no mascot mentioned at
    # all. Matching on the mascot alone (the old approach) silently never
    # matches those. Collect every word from the full team name instead,
    # so a hit on city OR mascot resolves the match.
    words = team_name.strip().lower().replace(".", "").split(" ")
    return {w for w in words if w and w not in _STOPWORDS}


def _team_in_text(tokens: set[str], text: str) -> bool:
    return any(re.search(rf"\b{re.escape(tok)}\b", text) for tok in tokens)


def _parse_iso(ts: str) -> float:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class TheOddsAPIProvider(SportsDataProvider):
    def __init__(self, api_key: str, market_data_adapter: ExchangeAdapter,
                 series_to_sport_key: dict | None = None, regions: str = "us",
                 markets: str = "h2h", match_window_hours: float = 12.0):
        if not api_key:
            raise RuntimeError("ODDS_API_KEY not set; cannot use TheOddsAPIProvider.")
        self.api_key = api_key
        self.market_data_adapter = market_data_adapter
        self.series_to_sport_key = series_to_sport_key or DEFAULT_SERIES_TO_SPORT_KEY
        self.regions = regions
        self.markets = markets
        self.match_window_hours = match_window_hours
        self._raw_by_event_id: dict[str, dict] = {}
        self._yes_team_by_event_id: dict[str, str] = {}

    def _fetch_odds(self, sport_key: str) -> list[dict]:
        resp = requests.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params={"apiKey": self.api_key, "regions": self.regions, "markets": self.markets, "oddsFormat": "american"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_upcoming_events(self, series_tickers: list[str], max_events: int) -> list[SportsEvent]:
        events: list[SportsEvent] = []
        for series_ticker in series_tickers:
            sport_key = self.series_to_sport_key.get(series_ticker)
            if sport_key is None:
                continue
            try:
                raw_events = self._fetch_odds(sport_key)
            except requests.RequestException:
                continue
            kalshi_markets = self.market_data_adapter.get_markets([series_ticker], limit=200)
            for raw in raw_events:
                match = self._match_kalshi_market(raw, kalshi_markets)
                if match is None:
                    continue
                market, yes_team = match
                self._raw_by_event_id[raw["id"]] = raw
                self._yes_team_by_event_id[raw["id"]] = yes_team
                events.append(SportsEvent(
                    event_id=raw["id"], league=raw.get("sport_title", sport_key),
                    home_team=raw["home_team"], away_team=raw["away_team"],
                    start_time_iso=raw["commence_time"], market_ticker=market.ticker,
                    outcome_description=f"{yes_team} wins (Kalshi YES side of {market.ticker})",
                ))
                if len(events) >= max_events:
                    return events
        return events

    def _match_kalshi_market(self, raw_event: dict, kalshi_markets: list[Market]) -> tuple[Market, str] | None:
        home_tokens = _team_tokens(raw_event["home_team"])
        away_tokens = _team_tokens(raw_event["away_team"])
        try:
            commence_ts = _parse_iso(raw_event["commence_time"])
        except (ValueError, KeyError):
            return None

        candidates = []
        for market in kalshi_markets:
            window_seconds = self.match_window_hours * 3600
            if abs(market.close_ts - commence_ts) > window_seconds:
                continue

            # Prefer Kalshi's yes_sub_title (a plain-language description of
            # what YES means for this specific market), when present, over
            # the market title -- many titles phrase things as "Team A vs
            # Team B" mentioning both teams, which the title-only check
            # below can never disambiguate.
            yes_sub_title = ((market.raw or {}).get("yes_sub_title") or "").lower()
            if yes_sub_title:
                home_in, away_in = _team_in_text(home_tokens, yes_sub_title), _team_in_text(away_tokens, yes_sub_title)
                if home_in and not away_in:
                    candidates.append((market, raw_event["home_team"]))
                    continue
                if away_in and not home_in:
                    candidates.append((market, raw_event["away_team"]))
                    continue
                # yes_sub_title present but didn't resolve it -- fall through
                # to the title heuristic rather than giving up immediately.

            title_lower = (market.title or "").lower()
            home_in, away_in = _team_in_text(home_tokens, title_lower), _team_in_text(away_tokens, title_lower)
            if home_in and not away_in:
                candidates.append((market, raw_event["home_team"]))
            elif away_in and not home_in:
                candidates.append((market, raw_event["away_team"]))
            # else: neither, or both, mentioned -- can't tell which side is
            # YES from title alone; skip this market rather than guess.

        if len(candidates) == 1:
            return candidates[0]
        return None  # zero or ambiguous multiple matches -- skip rather than guess

    def get_event_data(self, event: SportsEvent) -> SportsEventData:
        raw = self._raw_by_event_id.get(event.event_id)
        if raw is None:
            raise NotImplementedError(
                f"no cached odds payload for event {event.event_id}; call get_upcoming_events() first"
            )
        yes_team = self._yes_team_by_event_id.get(event.event_id, event.home_team)

        yes_team_prices, other_team_prices = [], []
        for bookmaker in raw.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome["name"] == yes_team:
                        yes_team_prices.append(outcome["price"])
                    else:
                        other_team_prices.append(outcome["price"])

        sportsbook_odds = {
            "yes_side_team": yes_team,
            "yes_side_moneyline_avg": sum(yes_team_prices) / len(yes_team_prices) if yes_team_prices else None,
            "other_side_moneyline_avg": sum(other_team_prices) / len(other_team_prices) if other_team_prices else None,
            "num_bookmakers": len(raw.get("bookmakers", [])),
        }

        devigged_yes_prob = None
        if yes_team_prices and other_team_prices:
            yes_p = _american_odds_to_prob(sportsbook_odds["yes_side_moneyline_avg"])
            other_p = _american_odds_to_prob(sportsbook_odds["other_side_moneyline_avg"])
            total = yes_p + other_p
            if total > 0:
                devigged_yes_prob = yes_p / total
        sportsbook_odds["devigged_yes_side_probability"] = devigged_yes_prob

        data_confidence = 1.0 if yes_team_prices else 0.3
        return SportsEventData(
            event=event,
            home_team_stats={}, away_team_stats={},  # TheOddsAPI has no stats endpoint; wire a stats API to fill these
            injuries=[],
            sportsbook_odds=sportsbook_odds,
            recent_form={},
            data_confidence=data_confidence,
            data_source_note=(
                "Odds from TheOddsAPI (h2h moneyline, averaged across "
                f"{sportsbook_odds['num_bookmakers']} bookmaker(s), vig removed). "
                "No team/player stats or injury data source configured."
            ),
        )
