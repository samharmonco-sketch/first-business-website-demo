"""Mispricing detection strategy for crypto interval markets.

Compares the market's implied probability (from its yes/no price) against a
simple reference probability computed from:
  - the current live spot price of the underlying (BTC/ETH) from a public
    reference API (Coinbase or Binance)
  - the market's strike/threshold (parsed from Kalshi's floor_strike /
    cap_strike fields when present, else from the market title)
  - time remaining to close
  - an assumed annualized volatility (config param -- a simplification;
    real implied vol would be better but requires an options data feed)

Reference probability model: treats "will BTC be above K at close" as a
digital/binary option and uses the standard lognormal approximation
P(S_T > K) = Phi( (ln(S/K)) / (sigma * sqrt(T)) ), ignoring drift over the
short interval (reasonable for sub-daily crypto windows). This is a
deliberately simple starter model, not a production options pricer.
"""
from __future__ import annotations

import math
import re
import time

import requests

from ..models import AccountState, Market, Signal, SignalAction
from .base import Strategy

_UNDERLYING_BY_SERIES = {
    "KXBTC": "BTC",
    "KXETH": "ETH",
}

_COINBASE_TICKER_URL = "https://api.coinbase.com/v2/prices/{pair}-USD/spot"
_BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"

_STRIKE_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)")


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class SpotPriceCache:
    """Fetches and caches spot prices for a single poll cycle so multiple
    markets for the same underlying don't each trigger a network call."""

    def __init__(self, source: str = "coinbase", ttl_seconds: float = 20.0):
        self.source = source
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, float]] = {}

    def get(self, symbol: str) -> float | None:
        now = time.time()
        cached = self._cache.get(symbol)
        if cached and (now - cached[1]) < self.ttl_seconds:
            return cached[0]
        price = self._fetch(symbol)
        if price is not None:
            self._cache[symbol] = (price, now)
        return price

    def _fetch(self, symbol: str) -> float | None:
        try:
            if self.source == "binance":
                resp = requests.get(_BINANCE_TICKER_URL, params={"symbol": f"{symbol}USDT"}, timeout=5)
                resp.raise_for_status()
                return float(resp.json()["price"])
            resp = requests.get(_COINBASE_TICKER_URL.format(pair=symbol), timeout=5)
            resp.raise_for_status()
            return float(resp.json()["data"]["amount"])
        except Exception:
            return None


class MispricingStrategy(Strategy):
    name = "mispricing"

    def __init__(self, params: dict):
        super().__init__(params)
        self.sigma = float(params.get("assumed_annualized_volatility", 0.55))
        self.min_ttl = float(params.get("min_time_to_expiry_seconds", 120))
        self.price_cache = SpotPriceCache(source=params.get("reference_price_source", "coinbase"))

    def _underlying_symbol(self, market: Market) -> str | None:
        return _UNDERLYING_BY_SERIES.get(market.series_ticker)

    def _extract_strike(self, market: Market) -> float | None:
        raw = market.raw or {}
        for key in ("floor_strike", "cap_strike", "strike"):
            if raw.get(key):
                try:
                    return float(raw[key])
                except (TypeError, ValueError):
                    pass
        match = _STRIKE_RE.search(market.title or "")
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                return None
        return None

    def evaluate(self, market: Market, account_state: AccountState) -> Signal:
        base_inputs = {
            "yes_mid": market.yes_mid, "seconds_to_close": market.seconds_to_close,
            "yes_ask": market.yes_ask, "yes_bid": market.yes_bid, "no_ask": market.no_ask,
        }

        symbol = self._underlying_symbol(market)
        if symbol is None:
            return self._hold(market, base_inputs, "market series is not a recognized crypto underlying")

        strike = self._extract_strike(market)
        if strike is None:
            return self._hold(market, base_inputs, "could not determine strike/threshold from market data or title")
        base_inputs["strike"] = strike

        ttl = market.seconds_to_close
        if ttl < self.min_ttl:
            return self._hold(market, base_inputs, f"time to close ({ttl:.0f}s) below minimum {self.min_ttl:.0f}s -- too close to expiry to model reliably")

        spot = self.price_cache.get(symbol)
        if spot is None:
            return self._hold(market, base_inputs, f"could not fetch live {symbol} spot price from reference source")
        base_inputs["spot_price"] = spot

        t_years = ttl / (365.0 * 24 * 3600)
        if spot <= 0 or strike <= 0 or t_years <= 0:
            return self._hold(market, base_inputs, "invalid inputs for probability model (non-positive spot/strike/time)")

        d = math.log(spot / strike) / (self.sigma * math.sqrt(t_years))
        model_prob_yes = _norm_cdf(d)
        implied_prob_yes = market.implied_yes_probability
        edge = model_prob_yes - implied_prob_yes
        base_inputs.update({"model_prob_yes": model_prob_yes, "implied_prob_yes": implied_prob_yes, "edge": edge})

        confidence = min(0.9, 0.5 + abs(edge) * 2)

        if edge > 0:
            action = SignalAction.BUY_YES
            reasoning = (f"mispricing: model probability of YES ({model_prob_yes:.3f}, spot={spot:.2f} vs "
                         f"strike={strike:.2f}, sigma={self.sigma}, t={ttl:.0f}s) exceeds market implied "
                         f"probability ({implied_prob_yes:.3f}) by {edge:.3f}")
        elif edge < 0:
            action = SignalAction.BUY_NO
            reasoning = (f"mispricing: model probability of YES ({model_prob_yes:.3f}) is below market implied "
                         f"probability ({implied_prob_yes:.3f}) by {abs(edge):.3f}, implying NO is underpriced")
        else:
            return self._hold(market, base_inputs, "model probability matches market price exactly; no edge")

        size_usd = round(10.0 + 40.0 * confidence, 2)
        return Signal(
            strategy_name=self.name, ticker=market.ticker, action=action,
            size_usd=size_usd, confidence=confidence, edge=abs(edge),
            reasoning=reasoning, inputs=base_inputs,
        )

    def _hold(self, market: Market, inputs: dict, reason: str) -> Signal:
        return Signal(
            strategy_name=self.name, ticker=market.ticker, action=SignalAction.HOLD,
            size_usd=0.0, confidence=0.0, edge=0.0, reasoning=reason, inputs=inputs,
        )
