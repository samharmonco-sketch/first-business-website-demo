"""Mispricing detection: compares Kalshi's market-implied probability against
a simple lognormal (GBM) volatility model driven by a real external reference
price (CoinGecko spot), per requirement #3. Works from the very first poll
cycle - no history needed - which is why day-one mode leans on this strategy
for the first visible trade.

Uses Kalshi's own numeric `floor_strike`/`strike_type` fields (confirmed
against the live API) rather than parsing the strike out of market text -
title text is kept only as a last-resort fallback for any market missing
those fields. Assumptions are fixed and logged plainly (this is deliberately
simple, not a production options-pricing model): zero drift (martingale) and
constant annualized volatility per asset.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..config import RiskConfig
from ..exchanges.spot_price import get_spot_price
from ..models import AccountState, MarketSnapshot, NoTradeDecision, OrderAction, Side, TradeSignal
from .base import Strategy

ASSUMED_ANNUAL_VOL = {
    "BTC": 0.55,
    "ETH": 0.65,
    "SOL": 0.90,
    "XRP": 0.85,
    "DOGE": 1.10,
    "LTC": 0.75,
    "ADA": 0.85,
    "AVAX": 0.95,
    "LINK": 0.85,
    "DOT": 0.90,
    "BNB": 0.65,
    "BCH": 0.80,
    "XLM": 0.90,
    "NEAR": 0.95,
    "TON": 0.90,
    "ZEC": 1.00,
    "HYPE": 1.10,
    "SHIB": 1.20,
}
STEADY_STATE_MIN_EDGE = 0.08
DEFAULT_TRADE_DOLLARS = 100.0

# "greater"/"greater_or_equal" markets carry the strike in floor_strike;
# "less"/"less_or_equal" carry it in cap_strike instead (confirmed against
# the live API - floor_strike is None on those). "between" markets (two-sided
# range contracts) aren't single-strike and are deliberately unsupported here.
ABOVE_STRIKE_TYPES = {"greater", "greater_or_equal"}
BELOW_STRIKE_TYPES = {"less", "less_or_equal"}


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


class CryptoMispricingStrategy(Strategy):
    name = "crypto_mispricing"

    def evaluate(
        self,
        market: MarketSnapshot,
        account: AccountState,
        risk: RiskConfig,
        day_one_mode: bool = False,
    ) -> TradeSignal | NoTradeDecision:
        asset = market.raw.get("_asset")
        if not asset:
            return NoTradeDecision(self.name, market.ticker, "market has no recognized crypto asset tag")

        strike_type = market.raw.get("strike_type", "")
        if strike_type in ABOVE_STRIKE_TYPES:
            strike, direction = market.raw.get("floor_strike"), "above"
        elif strike_type in BELOW_STRIKE_TYPES:
            strike, direction = market.raw.get("cap_strike"), "below"
        else:
            return NoTradeDecision(self.name, market.ticker, f"unsupported strike_type '{strike_type}', skipping (e.g. two-sided range contracts)")
        if strike is None:
            return NoTradeDecision(self.name, market.ticker, f"strike_type '{strike_type}' but no strike value present")
        strike = float(strike)

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
