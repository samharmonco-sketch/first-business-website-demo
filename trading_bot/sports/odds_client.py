"""The Odds API client - real sportsbook odds, used both as an input to the AI
sports analyst and to compute a no-vig consensus market probability."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger("trading_bot.odds")


@dataclass
class OddsEvent:
    event_id: str
    sport_key: str
    home_team: str
    away_team: str
    commence_time: str
    # no-vig consensus implied win probability, averaged across books
    home_implied_prob: float
    away_implied_prob: float
    bookmaker_count: int
    raw: dict = field(default_factory=dict)


class OddsApiClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get_events(self, sport_key: str) -> list[OddsEvent]:
        if not self.api_key:
            raise RuntimeError("ODDS_API_KEY is not set")
        url = f"{self.base_url}/sports/{sport_key}/odds"
        resp = requests.get(
            url,
            params={"apiKey": self.api_key, "regions": "us", "markets": "h2h", "oddsFormat": "american"},
            timeout=15,
        )
        resp.raise_for_status()
        events = []
        for ev in resp.json():
            home = ev.get("home_team")
            away = ev.get("away_team")
            home_probs, away_probs = [], []
            for book in ev.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    if home not in outcomes or away not in outcomes:
                        continue
                    p_home = self._american_to_prob(outcomes[home])
                    p_away = self._american_to_prob(outcomes[away])
                    total = p_home + p_away
                    if total <= 0:
                        continue
                    # remove vig by normalizing the two implied probs to sum to 1
                    home_probs.append(p_home / total)
                    away_probs.append(p_away / total)
            if not home_probs:
                continue
            events.append(
                OddsEvent(
                    event_id=ev.get("id", ""),
                    sport_key=sport_key,
                    home_team=home,
                    away_team=away,
                    commence_time=ev.get("commence_time", ""),
                    home_implied_prob=sum(home_probs) / len(home_probs),
                    away_implied_prob=sum(away_probs) / len(away_probs),
                    bookmaker_count=len(home_probs),
                    raw=ev,
                )
            )
        return events

    @staticmethod
    def _american_to_prob(price: float) -> float:
        price = float(price)
        if price > 0:
            return 100.0 / (price + 100.0)
        return -price / (-price + 100.0)
