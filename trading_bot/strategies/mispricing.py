"""Mispricing detection: compares Kalshi's market-implied probability against
a simple lognormal (GBM) volatility model driven by a real external reference
price (CoinGecko spot), per requirement #3. Works from the very first poll
cycle - no history needed - which is why day-one mode leans on this strategy
for the first visible trade.

Assumptions are fixed and logged plainly (this is deliberately simple, not a
production options-pricing model): zero drift (martingale), constant
annualized volatility per asset, and the strike/direction are parsed from the
market title's text.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from ..config import RiskConfig
from ..exchanges.spot_price import get_spot_price
from ..models import AccountState, MarketSnapshot, NoTradeDecision, OrderAction, Side, TradeSignal
from .base import Strategy

ASSUMED_ANNUAL_VOL = {"BTC": 0.55, "ETH": 0.65, "SOL": 0.90}
STEADY_STATE_MIN_EDGE = 0.08
DEFAULT_TRADE_DOLLARS = 100.0

ASSET_PATTERNS = [
    (re.compile(r"\bbitcoin\b|\bbtc\b", re.I), "BTC"),
    (re.compile(r"\bethereum\b|\beth\b", re.I), "ETH"),
    (re.compile(r"\bsolana\b|\bsol\b", re.I), "SOL"),
]
STRIKE_PATTERN = re.compile(r"\$?([\d,]+(?:\.\d+)?)")
ABOVE_WORDS = re.compile(r"\babove\b|\bor higher\b|\bor more\b|\bgreater\b|\babove or equal\b", re.I)
BELOW_WORDS = re.compile(r"\bbelow\b|\bor lower\b|\bor less\b|\bunder\b", re.I)


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _detect_asset(title: str) -> str | None:
    for pattern, symbol in ASSET_PATTERNS:
        if pattern.search(title):
            return symbol
    return None


def _parse_strike(title: str) -> float | None:
    matches = STRIKE_PATTERN.findall(title)
    if not matches:
        return None
    try:
        return float(matches[0].replace(",", ""))
    except ValueError:
        return None


def _direction(title: str) -> str:
    if BELOW_WORDS.search(title):
        return "below"
    return "above"  # default assumption when ambiguous, logged as such


class CryptoMispricingStrategy(Strategy):
    name = "crypto_mispricing"

    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = False,
    ) -> TradeSignal | NoTradeDecision:
        asset = _detect_asset(market.title)
        if not asset:
            return NoTradeDecision(self.name, market.ticker, "could not identify crypto asset from market title")

        strike = _parse_strike(market.title)
        if strike is None:
            return NoTradeDecision(self.name, market.ticker, f"could not parse a strike price from title '{market.title}'")

        spot = get_spot_price(asset)
        if spot is None:
            return NoTradeDecision(self.name, market.ticker, f"could not fetch live {asset} spot price from reference source")

        if not market.close_time:
            return NoTradeDecision(self.name, market.ticker, "market has no close_time, cannot compute time-to-expiry")
        try:
            close_dt = datetime.fromisoformat(market.close_time.replace("Z", "+00:00"))
        except ValueError:
            return NoTradeDecision(self.name, market.ticker, f"unparseable close_time '{market.close_time}'")

        seconds_to_close = (close_dt - datetime.now(timezone.utc)).total_seconds()
        if seconds_to_close <= 60:
            return NoTradeDecision(self.name, market.ticker, "market closes in <1 minute, too close to expiry to trade")

        t_years = seconds_to_close / (365.25 * 24 * 3600)
        vol = ASSUMED_ANNUAL_VOL.get(asset, 0.60)
        direction = _direction(market.title)

        z = (math.log(spot / strike) - 0.5 * vol**2 * t_years) / (vol * math.sqrt(t_years))
        model_prob_above = norm_cdf(z)
        model_prob_yes = model_prob_above if direction == "above" else (1 - model_prob_above)

        edge = model_prob_yes - market.implied_yes_prob
        min_edge = risk.day_one_min_edge if day_one_mode else STEADY_STATE_MIN_EDGE

        reasoning = (
            f"{asset} spot=${spot:,.2f}, strike=${strike:,.2f} ({direction}), "
            f"time_to_close={seconds_to_close/60:.1f}min, assumed_annual_vol={vol:.0%}, "
            f"model_prob_yes={model_prob_yes:.3f} vs market_implied_yes={market.implied_yes_prob:.3f}, "
            f"edge={edge:+.3f} (min_edge={min_edge:.3f}, day_one_mode={day_one_mode})"
        )

        if abs(edge) < min_edge:
            return NoTradeDecision(self.name, market.ticker, f"edge {edge:+.3f} below threshold {min_edge:.3f}. {reasoning}")

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
            confidence=min(0.95, abs(edge) * 3),
            reasoning=reasoning,
            edge=edge,
        )
