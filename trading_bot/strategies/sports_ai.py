"""AI sports analysis strategy: matches a Kalshi sports market to a live
odds-API event by team name, gets Claude's independent probability estimate,
and trades the edge against the market's implied probability. Every AI
prompt/response/parsed-number is logged (via extra) for auditability -
requirement #4 explicitly asked to see why the call was made, not just the
trade.
"""
from __future__ import annotations

import logging

from anthropic import Anthropic

from ..config import RiskConfig
from ..models import AccountState, MarketSnapshot, NoTradeDecision, OrderAction, Side, TradeSignal
from ..sports.ai_analyst import analyze
from ..sports.odds_client import SPORT_KEYWORDS, OddsApiClient, OddsEvent
from .base import Strategy

logger = logging.getLogger("trading_bot.sports_ai")

STEADY_STATE_MIN_EDGE = 0.07
DEFAULT_TRADE_DOLLARS = 100.0


class SportsAiStrategy(Strategy):
    name = "sports_ai"

    def __init__(self, odds_client: OddsApiClient, anthropic_client: Anthropic, model: str):
        self.odds_client = odds_client
        self.anthropic_client = anthropic_client
        self.model = model
        self._event_cache: dict[str, list[OddsEvent]] = {}

    def _sport_keys_for(self, market: MarketSnapshot) -> list[str]:
        text = f"{market.title} {market.series_ticker or ''} {market.raw.get('category', '')}".lower()
        return [key for kw, key in SPORT_KEYWORDS.items() if kw in text] or list(SPORT_KEYWORDS.values())

    def _events_for_sport(self, sport_key: str) -> list[OddsEvent]:
        if sport_key not in self._event_cache:
            try:
                self._event_cache[sport_key] = self.odds_client.get_events(sport_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("odds fetch failed for %s: %s", sport_key, exc)
                self._event_cache[sport_key] = []
        return self._event_cache[sport_key]

    def _match_event(self, market: MarketSnapshot) -> OddsEvent | None:
        title_lower = market.title.lower()
        for sport_key in self._sport_keys_for(market):
            for event in self._events_for_sport(sport_key):
                if event.home_team.lower() in title_lower and event.away_team.lower() in title_lower:
                    return event
        return None

    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = False,
    ) -> TradeSignal | NoTradeDecision:
        event = self._match_event(market)
        if not event:
            return NoTradeDecision(self.name, market.ticker, "no matching odds-API event found for this market's teams")

        result = analyze(self.anthropic_client, self.model, event)
        if not result:
            return NoTradeDecision(self.name, market.ticker, "AI sports analysis call failed or returned unparseable output")

        # Which side of the Kalshi market is "home team wins"?
        home_first = market.title.lower().find(event.home_team.lower())
        away_first = market.title.lower().find(event.away_team.lower())
        yes_means_home = home_first != -1 and (away_first == -1 or home_first < away_first)

        model_prob_yes = result.home_win_probability if yes_means_home else (1 - result.home_win_probability)
        edge = model_prob_yes - market.implied_yes_prob
        min_edge = risk.day_one_min_edge if day_one_mode else STEADY_STATE_MIN_EDGE

        reasoning = (
            f"{event.away_team} @ {event.home_team}: AI home_win_prob={result.home_win_probability:.3f} "
            f"(confidence={result.confidence:.2f}) vs market consensus home_implied={event.home_implied_prob:.3f} "
            f"[{event.bookmaker_count} books]. Kalshi yes_means_home={yes_means_home}, "
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
