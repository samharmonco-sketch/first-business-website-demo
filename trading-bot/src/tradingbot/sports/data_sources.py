"""Pluggable sports data sourcing.

Real production use requires a real stats/odds/injury data provider (e.g.
TheOddsAPI, SportsDataIO, a sportsbook's own API). This module defines the
interface (`SportsDataProvider`) plus a `StubSportsDataProvider` that
returns clearly-labeled placeholder data so the AI sports strategy is
fully wired end-to-end and testable in paper mode without a paid API key.

Swap in a real provider by implementing `SportsDataProvider` against your
chosen API and passing it to `SportsAIStrategy` -- the strategy and
analyzer code never need to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SportsEvent:
    event_id: str
    league: str
    home_team: str
    away_team: str
    start_time_iso: str
    market_ticker: str
    outcome_description: str  # e.g. "home team wins", "total over 220.5"


@dataclass
class SportsEventData:
    event: SportsEvent
    home_team_stats: dict = field(default_factory=dict)
    away_team_stats: dict = field(default_factory=dict)
    injuries: list[str] = field(default_factory=list)
    # Sportsbook odds are LLM input context only (e.g. {"home_moneyline_avg": -140,
    # "away_moneyline_avg": 120}) -- they are NOT the trade edge comparison.
    sportsbook_odds: dict = field(default_factory=dict)
    recent_form: dict = field(default_factory=dict)
    data_confidence: float = 1.0  # how much to trust this data (1.0 = real, verified data)
    data_source_note: str = ""


class SportsDataProvider(ABC):
    @abstractmethod
    def get_upcoming_events(self, series_tickers: list[str], max_events: int) -> list[SportsEvent]:
        ...

    @abstractmethod
    def get_event_data(self, event: SportsEvent) -> SportsEventData:
        ...


class StubSportsDataProvider(SportsDataProvider):
    """Placeholder provider. Returns no events by default so the sports_ai
    strategy is a safe no-op until a real data provider is configured --
    it must never silently fabricate data that gets treated as real market
    intelligence. Wire in a real provider before enabling sports trading."""

    def get_upcoming_events(self, series_tickers: list[str], max_events: int) -> list[SportsEvent]:
        return []

    def get_event_data(self, event: SportsEvent) -> SportsEventData:
        raise NotImplementedError(
            "StubSportsDataProvider has no real data. Implement SportsDataProvider "
            "against a real stats/odds/injury API before enabling sports_ai in production."
        )
