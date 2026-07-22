"""Strategy registry: maps config strategy names to implementation classes.

Adding a new strategy module = write the class + add one line here.
"""
from __future__ import annotations

from pathlib import Path

from ..adapters.base import ExchangeAdapter
from .base import Strategy
from .crypto_momentum import CryptoMomentumStrategy
from .dummy_buy import DummyAlwaysSmallBuyStrategy
from .mispricing import MispricingStrategy
from .sports_ai import SportsAIStrategy

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "dummy_buy": DummyAlwaysSmallBuyStrategy,
    "crypto_momentum": CryptoMomentumStrategy,
    "mispricing": MispricingStrategy,
    "sports_ai": SportsAIStrategy,
}


def _build_sports_data_provider(params: dict, odds_api_key: str, market_data_adapter: ExchangeAdapter | None):
    if not odds_api_key or market_data_adapter is None:
        from ..sports.data_sources import StubSportsDataProvider
        return StubSportsDataProvider()
    from ..sports.odds_api_provider import TheOddsAPIProvider
    return TheOddsAPIProvider(
        api_key=odds_api_key,
        market_data_adapter=market_data_adapter,
        series_to_sport_key=params.get("series_to_sport_key"),
        match_window_hours=float(params.get("match_window_hours", 12.0)),
    )


def build_strategies(strategies_config: dict, anthropic_config=None, log_dir: Path | None = None,
                      odds_api_key: str = "", market_data_adapter: ExchangeAdapter | None = None) -> list[Strategy]:
    enabled_names = strategies_config.get("enabled", [])
    instances: list[Strategy] = []
    for name in enabled_names:
        cls = STRATEGY_REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"Unknown strategy in config: {name}")
        params = strategies_config.get(name, {})
        if not params.get("enabled", True):
            continue
        if name == "sports_ai":
            data_provider = _build_sports_data_provider(params, odds_api_key, market_data_adapter)
            instances.append(cls(
                params, anthropic_config=anthropic_config, log_dir=log_dir,
                data_provider=data_provider, market_data_adapter=market_data_adapter,
            ))
        else:
            instances.append(cls(params))
    return instances
