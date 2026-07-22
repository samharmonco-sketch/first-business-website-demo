"""Strategy registry: maps config strategy names to implementation classes.

Adding a new strategy module = write the class + add one line here.
"""
from __future__ import annotations

from pathlib import Path

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


def build_strategies(strategies_config: dict, anthropic_config=None, log_dir: Path | None = None) -> list[Strategy]:
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
            instances.append(cls(params, anthropic_config=anthropic_config, log_dir=log_dir))
        else:
            instances.append(cls(params))
    return instances
