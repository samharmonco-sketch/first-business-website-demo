"""Mispricing detection: compares Kalshi's market-implied probability against
a simple lognormal (GBM) volatility model driven by a real external reference
price (CoinGecko spot), per requirement #3. Works from the very first poll
cycle - no history needed - which is why day-one mode leans on this strategy
for the first visible trade.

Volatility is derived from the market itself, not guessed: each event's
at-the-money strike (closest to spot, and typically the most liquid /
efficiently priced) is used to back out an implied vol, which then prices
every other strike in that same event. A first version used fixed constants
per asset instead - real settlement data showed those were 1.5-2x too high
across BTC/ETH/XRP/SOL simultaneously, and specifically inverted calibration
(the highest-confidence trades had the worst win rate, since bigger assumed-
vs-market vol gaps produced both the biggest apparent edge and the biggest
actual error). Betting on cross-strike inconsistencies within one event's own
pricing is a much narrower, more defensible claim than betting the whole
market's volatility level is wrong. The fixed constants remain only as a
fallback for the rare event with no usable ATM strike.

Uses Kalshi's own numeric `floor_strike`/`strike_type` fields (confirmed
against the live API) rather than parsing the strike out of market text -
title text is kept only as a last-resort fallback for any market missing
those fields.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..config import RiskConfig
from ..exchanges.spot_price import get_spot_price
from ..models import AccountState, MarketSnapshot, NoTradeDecision, OrderAction, Side, TradeSignal
from .base import Strategy

# Fallback only - used when an event has no usable ATM strike to derive vol
# from (e.g. only one strike currently listed). See module docstring: these
# fixed guesses are what caused the inverse-calibration problem in the first
# place, so the market-derived vol in compute_atm_vols() is always preferred.
FALLBACK_ANNUAL_VOL = {
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


def norm_ppf(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    lo, hi = -8.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _strike_and_direction(market: MarketSnapshot) -> tuple[float, str] | None:
    strike_type = market.raw.get("strike_type", "")
    if strike_type in ABOVE_STRIKE_TYPES:
        strike = market.raw.get("floor_strike")
        direction = "above"
    elif strike_type in BELOW_STRIKE_TYPES:
        strike = market.raw.get("cap_strike")
        direction = "below"
    else:
        return None
    if strike is None:
        return None
    return float(strike), direction


def _seconds_to_close(market: MarketSnapshot) -> float | None:
    if not market.close_time:
        return None
    try:
        close_dt = datetime.fromisoformat(market.close_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (close_dt - datetime.now(timezone.utc)).total_seconds()


VOL_LO, VOL_HI = 0.02, 5.0
# A solution that lands within this margin of either search bound almost
# always means the market price was degenerate (near 0% or 100%, typically a
# thinly-traded or just-opened strike) rather than a genuine extreme vol -
# treat it as "couldn't solve" rather than trusting a nonsense number.
VOL_BOUNDARY_MARGIN = 0.05


def implied_vol_from_price(spot: float, strike: float, direction: str, t_years: float, market_prob_yes: float) -> float | None:
    """Back out the annualized vol that makes the lognormal model agree with
    the market's current price for one strike - the inverse of the pricing
    formula below."""
    if t_years <= 0 or spot <= 0 or strike <= 0:
        return None
    market_prob_above = market_prob_yes if direction == "above" else (1 - market_prob_yes)
    if market_prob_above < 0.03 or market_prob_above > 0.97:
        return None  # too close to 0/100c to trust - see module note on degenerate quotes
    z_target = norm_ppf(market_prob_above)
    ln_sk = math.log(spot / strike)
    sqrt_t = math.sqrt(t_years)

    def f(vol: float) -> float:
        return (ln_sk - 0.5 * vol**2 * t_years) / (vol * sqrt_t) - z_target

    lo, hi = VOL_LO, VOL_HI
    f_lo = f(lo)
    for _ in range(60):
        mid = (lo + hi) / 2
        f_mid = f(mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    solved = (lo + hi) / 2
    if solved <= VOL_LO + VOL_BOUNDARY_MARGIN or solved >= VOL_HI - VOL_BOUNDARY_MARGIN:
        return None
    return solved


def compute_atm_vols(crypto_markets: list[MarketSnapshot]) -> dict[str, float]:
    """One implied vol per underlying event, derived from its at-the-money
    strike. Called once per cycle by the engine and stamped onto each
    market's raw['_implied_vol'] - see module docstring for why."""
    by_event: dict[str, list[MarketSnapshot]] = {}
    for m in crypto_markets:
        event = m.raw.get("event_ticker")
        if event:
            by_event.setdefault(event, []).append(m)

    vols: dict[str, float] = {}
    for event, markets in by_event.items():
        asset = markets[0].raw.get("_asset")
        if not asset:
            continue
        spot = get_spot_price(asset)
        if spot is None:
            continue

        candidates = []
        for m in markets:
            parsed = _strike_and_direction(m)
            if not parsed:
                continue
            strike, direction = parsed
            candidates.append((abs(strike - spot), m, strike, direction))
        candidates.sort(key=lambda c: c[0])

        # Try strikes nearest-to-spot first, falling through to the next one
        # if the closest has a degenerate (near 0%/100%) quote that can't be
        # trusted to solve for vol - a thin/just-opened ATM strike shouldn't
        # mean the whole event gets no market-derived vol at all.
        for _, m, strike, direction in candidates:
            seconds_to_close = _seconds_to_close(m)
            if seconds_to_close is None or seconds_to_close <= 60:
                continue
            t_years = seconds_to_close / (365.25 * 24 * 3600)
            vol = implied_vol_from_price(spot, strike, direction, t_years, m.implied_yes_prob)
            if vol is not None:
                vols[event] = vol
                break
    return vols


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

        parsed = _strike_and_direction(market)
        if not parsed:
            strike_type = market.raw.get("strike_type", "")
            return NoTradeDecision(self.name, market.ticker, f"unsupported strike_type '{strike_type}', skipping (e.g. two-sided range contracts)")
        strike, direction = parsed

        spot = get_spot_price(asset)
        if spot is None:
            return NoTradeDecision(self.name, market.ticker, f"could not fetch live {asset} spot price from reference source")

        seconds_to_close = _seconds_to_close(market)
        if seconds_to_close is None:
            return NoTradeDecision(self.name, market.ticker, "market has no usable close_time, cannot compute time-to-expiry")
        if seconds_to_close <= 60:
            return NoTradeDecision(self.name, market.ticker, "market closes in <1 minute, too close to expiry to trade")

        t_years = seconds_to_close / (365.25 * 24 * 3600)
        vol = market.raw.get("_implied_vol")
        vol_source = "market-derived (event ATM strike)"
        if vol is None:
            vol = FALLBACK_ANNUAL_VOL.get(asset, 0.60)
            vol_source = "fallback fixed guess (no ATM strike available)"

        z = (math.log(spot / strike) - 0.5 * vol**2 * t_years) / (vol * math.sqrt(t_years))
        model_prob_above = norm_cdf(z)
        model_prob_yes = model_prob_above if direction == "above" else (1 - model_prob_above)

        edge = model_prob_yes - market.implied_yes_prob
        min_edge = risk.day_one_min_edge if day_one_mode else STEADY_STATE_MIN_EDGE

        reasoning = (
            f"{asset} spot=${spot:,.2f}, strike=${strike:,.2f} ({direction}), "
            f"time_to_close={seconds_to_close/60:.1f}min, vol={vol:.0%} [{vol_source}], "
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
