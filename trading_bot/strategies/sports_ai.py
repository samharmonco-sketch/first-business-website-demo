"""AI sports analysis strategy: Kalshi runs one binary market per team per
game (confirmed against the live API - "Team A vs Team B Winner?" is actually
two separate YES/NO markets, one per team, not a single home/away market).
Kalshi's own no_sub_title field is USELESS for identifying the opponent - it
is always identical to yes_sub_title (confirmed against the live API), so the
real opponent name comes from the sibling market sharing the same
event_ticker, which the Kalshi adapter stamps onto raw['_opponent']. Matching
requires BOTH this market's team AND its real opponent to correctly pair
against the same odds-API event's home/away teams - matching on either team
name alone is exactly what let same-state collisions ("Texas" matching
"Texas State" in an unrelated game) silently trade the wrong matchup.

Every AI prompt/response/parsed-number is logged (via extra) for
auditability - requirement #4 explicitly asked to see why the call was made,
not just the trade.
"""
from __future__ import annotations

import logging

from anthropic import Anthropic

from ..config import RiskConfig
from ..models import AccountState, MarketSnapshot, NoTradeDecision, OrderAction, Side, TradeSignal
from ..sports.ai_analyst import analyze
from ..sports.odds_client import OddsApiClient, OddsEvent
from .base import Strategy

logger = logging.getLogger("trading_bot.sports_ai")

STEADY_STATE_MIN_EDGE = 0.07
DEFAULT_TRADE_DOLLARS = 100.0


def _team_match(short_name: str, full_name: str) -> bool:
    s = (short_name or "").lower().strip()
    f = (full_name or "").lower().strip()
    if not s or not f:
        return False
    return s in f or f in s


class SportsAiStrategy(Strategy):
    name = "sports_ai"

    def __init__(self, odds_client: OddsApiClient, anthropic_client: Anthropic, model: str):
        self.odds_client = odds_client
        self.anthropic_client = anthropic_client
        self.model = model
        self._event_cache: dict[str, list[OddsEvent]] = {}

    def _events_for_sport(self, sport_key: str) -> list[OddsEvent]:
        if sport_key not in self._event_cache:
            try:
                self._event_cache[sport_key] = self.odds_client.get_events(sport_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("odds fetch failed for %s: %s", sport_key, exc)
                self._event_cache[sport_key] = []
        return self._event_cache[sport_key]

    def _match_event(self, market: MarketSnapshot, yes_team: str, opponent_team: str) -> OddsEvent | None:
        sport_key = market.raw.get("_sport_key")
        if not sport_key or not opponent_team:
            return None
        for event in self._events_for_sport(sport_key):
            yes_is_home = _team_match(yes_team, event.home_team)
            yes_is_away = _team_match(yes_team, event.away_team)
            if not (yes_is_home or yes_is_away):
                continue
            other_team = event.away_team if yes_is_home else event.home_team
            if _team_match(opponent_team, other_team):
                return event
        return None

    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = False,
    ) -> TradeSignal | NoTradeDecision:
        yes_team = market.raw.get("yes_sub_title", "")
        opponent_team = market.raw.get("_opponent", "")
        if not yes_team:
            return NoTradeDecision(self.name, market.ticker, "market has no yes_sub_title team name to match against")
        if not opponent_team:
            return NoTradeDecision(self.name, market.ticker, f"could not determine '{yes_team}''s opponent from sibling market")

        event = self._match_event(market, yes_team, opponent_team)
        if not event:
            return NoTradeDecision(self.name, market.ticker, f"no matching odds-API event found for '{yes_team}' vs '{opponent_team}'")

        result = analyze(self.anthropic_client, self.model, event)
        if not result:
            return NoTradeDecision(self.name, market.ticker, "AI sports analysis call failed or returned unparseable output")

        yes_is_home = _team_match(yes_team, event.home_team)
        model_prob_yes = result.home_win_probability if yes_is_home else (1 - result.home_win_probability)
        edge = model_prob_yes - market.implied_yes_prob
        min_edge = risk.day_one_min_edge if day_one_mode else STEADY_STATE_MIN_EDGE

        reasoning = (
            f"{event.away_team} @ {event.home_team}: this market's YES = '{yes_team}' wins. "
            f"AI home_win_prob={result.home_win_probability:.3f} (confidence={result.confidence:.2f}) vs "
            f"market consensus home_implied={event.home_implied_prob:.3f} [{event.bookmaker_count} books]. "
            f"model_prob_yes={model_prob_yes:.3f} vs kalshi_implied_yes={market.implied_yes_prob:.3f}, "
            f"edge={edge:+.3f} (min_edge={min_edge:.3f}). AI reasoning: {result.reasoning}"
        )
        extra = {
            "ai_prompt": result.prompt,
            "ai_raw_response": result.raw_response,
            "ai_home_win_probability": result.home_win_probability,
            "ai_confidence": result.confidence,
            "market_consensus_home_implied": event.home_implied_prob,
            "bookmaker_count": event.bookmaker_count,
        }

        if abs(edge) < min_edge or result.confidence < 0.35:
            reason = f"edge {edge:+.3f} below threshold {min_edge:.3f} or low AI confidence ({result.confidence:.2f}). {reasoning}"
            return NoTradeDecision(self.name, market.ticker, reason)

        max_dollars = risk.day_one_max_position_abs if day_one_mode else DEFAULT_TRADE_DOLLARS
        side = Side.YES if edge > 0 else Side.NO
        price_cents = market.yes_ask if side == Side.YES else market.no_ask
        if price_cents <= 0 or price_cents >= 100:
            return NoTradeDecision(self.name, market.ticker, f"no valid ask price on {side.value} side. {reasoning}")
        contracts = max(1, int(max_dollars * 100 / price_cents))

        return TradeSignal(
            strategy=self.name,
            ticker=market.ticker,
            action=OrderAction.BUY,
            side=side,
            size_contracts=contracts,
            limit_price_cents=price_cents,
            confidence=result.confidence,
            reasoning=reasoning,
            edge=edge,
            extra=extra,
        )
