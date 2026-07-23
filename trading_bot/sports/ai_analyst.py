"""AI sports analysis, per requirement #4: given an upcoming matchup, ask
Claude for an independent probability estimate + confidence score, distinct
from the raw bookmaker consensus. Every prompt and response is returned
alongside the parsed numbers so the caller can log them verbatim - the point
is auditability ("I want to see why it made a call"), not just a number.
"""
from __future__ import annotations

import json
import logging
import re

from anthropic import Anthropic

from .odds_client import OddsEvent

logger = logging.getLogger("trading_bot.ai_analyst")

SYSTEM_PROMPT = (
    "You are a sports betting analyst producing a calibrated probability estimate "
    "for a prediction market. You are given the matchup and the current no-vig "
    "consensus probability implied by sportsbook odds. No dedicated injury/stats "
    "feed is wired up yet, so rely on your general knowledge of the teams, "
    "typical home-field/ice/court advantage, and recent form you're aware of, "
    "combined with the given market odds as a strong prior. Respond with ONLY a "
    "JSON object: {\"home_win_probability\": <0-1 float>, \"confidence\": <0-1 float>, "
    "\"reasoning\": \"<2-4 sentences>\"}. confidence should reflect how much you know "
    "about this specific matchup, not just how extreme the probability is."
)


class AiAnalystResult:
    def __init__(self, home_win_probability: float, confidence: float, reasoning: str, prompt: str, raw_response: str):
        self.home_win_probability = home_win_probability
        self.confidence = confidence
        self.reasoning = reasoning
        self.prompt = prompt
        self.raw_response = raw_response


def _build_prompt(event: OddsEvent) -> str:
    return (
        f"Matchup: {event.away_team} @ {event.home_team}\n"
        f"Sport: {event.sport_key}\n"
        f"Commence time: {event.commence_time}\n"
        f"Consensus market-implied (no-vig, averaged across {event.bookmaker_count} books):\n"
        f"  {event.home_team} win probability: {event.home_implied_prob:.3f}\n"
        f"  {event.away_team} win probability: {event.away_implied_prob:.3f}\n\n"
        "Give your own independent probability that the home team wins."
    )


def analyze(client: Anthropic, model: str, event: OddsEvent) -> AiAnalystResult | None:
    prompt = _build_prompt(event)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=700,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Anthropic call failed for %s @ %s: %s", event.away_team, event.home_team, exc)
        return None

    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        logger.error("Could not parse JSON from AI sports response: %s", raw_text[:300])
        return None
    try:
        parsed = json.loads(match.group(0))
        return AiAnalystResult(
            home_win_probability=float(parsed["home_win_probability"]),
            confidence=float(parsed["confidence"]),
            reasoning=str(parsed.get("reasoning", "")),
            prompt=prompt,
            raw_response=raw_text,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Malformed AI sports JSON: %s (%s)", raw_text[:300], exc)
        return None
