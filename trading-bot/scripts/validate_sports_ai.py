#!/usr/bin/env python3
"""Standalone dry-run of the sports_ai strategy against REAL data: fetches
real upcoming events + odds from TheOddsAPI, matches them to real open
Kalshi markets, calls Claude for a probability/confidence estimate, and
prints each one next to the market's implied odds so you can sanity-check
the reasoning before trusting it with money.

This does NOT place any orders -- it only calls evaluate_events() and
prints the resulting signals. Run from the trading-bot/ directory:

    source .venv/bin/activate
    python scripts/validate_sports_ai.py

Requires in your environment (.env or exported):
    ODDS_API_KEY
    ANTHROPIC_API_KEY
    (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH are NOT required -- market
    data lookups use Kalshi's public, unauthenticated endpoints only)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from tradingbot.adapters.kalshi import KalshiAdapter  # noqa: E402
from tradingbot.config import AnthropicConfig, ExchangeConfig, load_config  # noqa: E402
from tradingbot.sports.odds_api_provider import TheOddsAPIProvider  # noqa: E402
from tradingbot.strategies.sports_ai import SportsAIStrategy  # noqa: E402


def main() -> int:
    odds_api_key = os.environ.get("ODDS_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    print("=" * 70)
    print(" SPORTS_AI DRY RUN (real TheOddsAPI + real Kalshi market data,")
    print(" real Claude calls -- no orders placed)")
    print("=" * 70)

    if not odds_api_key:
        print("ERROR: ODDS_API_KEY not set in environment/.env.")
        return 1
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment/.env -- Claude calls will fail.")
        return 1

    config = load_config(config_path=ROOT / "config" / "config.yaml", root_dir=ROOT)
    market_data_adapter = KalshiAdapter(config.exchange)  # public data only, no auth needed
    sports_params = config.strategies.get("sports_ai", {})

    provider = TheOddsAPIProvider(
        api_key=odds_api_key, market_data_adapter=market_data_adapter,
        series_to_sport_key=sports_params.get("series_to_sport_key"),
        match_window_hours=float(sports_params.get("match_window_hours", 12.0)),
    )
    strategy = SportsAIStrategy(
        sports_params, anthropic_config=config.anthropic, data_provider=provider,
        market_data_adapter=market_data_adapter, log_dir=config.resolve_path(config.logging.log_dir),
    )

    print(f"Sports series being checked: {config.polling.sports_series_tickers}")
    print()

    # evaluate_events() silently skips a series if its TheOddsAPI fetch fails
    # (sane behavior for a long-running bot -- one bad league shouldn't kill
    # the cycle), which would hide the real error here. Probe each series'
    # raw fetch first so failures are visible during validation.
    print("[pre-check] Testing raw TheOddsAPI connectivity per configured series...")
    for series_ticker in config.polling.sports_series_tickers:
        sport_key = provider.series_to_sport_key.get(series_ticker)
        if sport_key is None:
            print(f"  {series_ticker}: SKIP -- no series_to_sport_key mapping configured")
            continue
        try:
            raw_events = provider._fetch_odds(sport_key)
            print(f"  {series_ticker} ({sport_key}): PASS -- {len(raw_events)} event(s) from TheOddsAPI")
        except Exception as exc:
            print(f"  {series_ticker} ({sport_key}): FAIL -- {exc}")
    print()

    print("Fetching events, matching to Kalshi markets, calling Claude for each match...")
    print()

    signals = strategy.evaluate_events(config.polling.sports_series_tickers)

    if not signals:
        print("No signals returned at all (unexpected -- evaluate_events should always return at least one HOLD).")
        return 1

    for i, s in enumerate(signals, 1):
        print("-" * 70)
        print(f"[{i}] {s.ticker}  action={s.action.value}  edge={s.edge:+.3f}  confidence={s.confidence:.2f}")
        if "ai_probability" in s.inputs:
            print(f"    AI probability estimate:      {s.inputs['ai_probability']:.3f}")
            print(f"    Kalshi market implied prob:    {s.inputs.get('kalshi_implied_probability', float('nan')):.3f}")
            sb = s.inputs.get("sportsbook_odds", {})
            if sb.get("devigged_yes_side_probability") is not None:
                print(f"    Sportsbook devigged prob:      {sb['devigged_yes_side_probability']:.3f}  "
                      f"(from {sb.get('num_bookmakers', 0)} bookmaker(s))")
        print(f"    Reasoning: {s.reasoning}")
    print("-" * 70)
    print()
    print(f"Total events evaluated: {len(signals)}")
    print(f"Actionable signals (would pass to risk manager): {sum(1 for s in signals if s.is_actionable)}")
    print()
    print(f"Full prompt/response audit trail: {config.resolve_path(config.logging.log_dir) / 'sports_audit.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
