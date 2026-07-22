"""AI sports analysis: calls Claude (Anthropic API) to produce a probability
estimate + confidence score for a sports market outcome, given team/player
stats, injuries, sportsbook odds, and recent form.

Every prompt and raw response is logged for auditability (config
logging.log_dir/sports_audit.jsonl) -- the goal is to be able to see
*why* the model made a call, not just the resulting trade.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import AnthropicConfig
from .data_sources import SportsEventData

SYSTEM_PROMPT = (
    "You are a sports analytics assistant supporting a prediction-market trading bot. "
    "Given structured data about an upcoming sporting event, estimate the probability "
    "of the specified outcome and how confident you are in that estimate. "
    "Base your estimate only on the data provided -- do not assume access to information "
    "not given to you, and say so in your reasoning if the data is thin. "
    "Respond with ONLY a JSON object, no other text, in this exact shape:\n"
    '{"probability": <float 0-1, probability the outcome occurs>, '
    '"confidence": <float 0-1, your confidence in this estimate>, '
    '"reasoning": "<concise explanation citing the specific inputs that drove your estimate>"}'
)


@dataclass
class AIAnalysisResult:
    probability: float
    confidence: float
    reasoning: str
    raw_response: str
    prompt: str


class SportsAnalyzer:
    def __init__(self, config: AnthropicConfig, audit_log_path: Path):
        self.config = config
        self.audit_log_path = audit_log_path
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            if not self.config.api_key:
                raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run sports_ai strategy.")
            self._client = anthropic.Anthropic(api_key=self.config.api_key)
        return self._client

    def _build_prompt(self, data: SportsEventData) -> str:
        event = data.event
        payload = {
            "league": event.league,
            "matchup": f"{event.away_team} @ {event.home_team}",
            "outcome_to_estimate": event.outcome_description,
            "home_team_stats": data.home_team_stats,
            "away_team_stats": data.away_team_stats,
            "injuries": data.injuries,
            "sportsbook_odds": data.sportsbook_odds,
            "recent_form": data.recent_form,
            "data_confidence_note": data.data_source_note,
        }
        return json.dumps(payload, indent=2)

    def analyze(self, data: SportsEventData) -> AIAnalysisResult | None:
        prompt = self._build_prompt(data)
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
        except Exception as exc:
            self._audit(prompt, f"ERROR: {exc}", None, data)
            return None

        parsed = self._parse_response(raw_text)
        self._audit(prompt, raw_text, parsed, data)
        if parsed is None:
            return None
        return AIAnalysisResult(
            probability=parsed["probability"], confidence=parsed["confidence"],
            reasoning=parsed["reasoning"], raw_response=raw_text, prompt=prompt,
        )

    @staticmethod
    def _parse_response(raw_text: str) -> dict | None:
        try:
            start = raw_text.index("{")
            end = raw_text.rindex("}") + 1
            obj = json.loads(raw_text[start:end])
            prob = float(obj["probability"])
            conf = float(obj["confidence"])
            reasoning = str(obj.get("reasoning", ""))
            if not (0.0 <= prob <= 1.0) or not (0.0 <= conf <= 1.0):
                return None
            return {"probability": prob, "confidence": conf, "reasoning": reasoning}
        except (ValueError, KeyError, json.JSONDecodeError):
            return None

    def _audit(self, prompt: str, raw_response: str, parsed: dict | None, data: SportsEventData) -> None:
        entry = {
            "ts": time.time(),
            "event_id": data.event.event_id,
            "market_ticker": data.event.market_ticker,
            "outcome_description": data.event.outcome_description,
            "prompt": prompt,
            "raw_response": raw_response,
            "parsed": parsed,
        }
        with open(self.audit_log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
