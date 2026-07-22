"""Reference spot price for crypto mispricing detection. Uses CoinGecko's
public, keyless API - this is the "simple external reference price" the
mispricing strategy compares Kalshi's implied probability against.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger("trading_bot.spot_price")

COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
_cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, price)
CACHE_TTL_SECONDS = 30


def get_spot_price(symbol: str) -> float | None:
    import time

    symbol = symbol.upper()
    now = time.time()
    if symbol in _cache and now - _cache[symbol][0] < CACHE_TTL_SECONDS:
        return _cache[symbol][1]

    coingecko_id = COINGECKO_IDS.get(symbol)
    if not coingecko_id:
        return None
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coingecko_id, "vs_currencies": "usd"},
            timeout=10,
        )
        resp.raise_for_status()
        price = resp.json()[coingecko_id]["usd"]
        _cache[symbol] = (now, float(price))
        return float(price)
    except Exception as exc:  # noqa: BLE001
        logger.warning("spot price fetch failed for %s: %s", symbol, exc)
        return None
