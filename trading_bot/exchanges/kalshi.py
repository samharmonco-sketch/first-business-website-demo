"""Kalshi adapter. Kalshi auth is API-Key-ID + RSA private key: every request is
signed (RSA-PSS/SHA256 over `timestamp + method + path`) and sent as the
KALSHI-ACCESS-* headers, per Kalshi's documented API-key auth scheme -
there is no plain bearer-token option.

Market data always comes from the real Kalshi API, in both paper and live
mode. Only order placement is gated by config.mode: in paper mode this
adapter is used purely for get_markets/get_orderbook, and the PaperBroker
(see paper.py) does the simulated fills instead of calling place_order here.
"""
from __future__ import annotations

import base64
import logging
import time
import uuid

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import KalshiConfig
from ..models import MarketCategory, MarketSnapshot, Order, OrderAction, Side
from .base import ExchangeAdapter
from .spot_price import COINGECKO_IDS

logger = logging.getLogger("trading_bot.kalshi")

# Kalshi's /series endpoint is the reliable way to find crypto interval markets
# (requirement #1's "BTC up/down in the next hour/15min") - these frequencies
# are the actual short-horizon interval contracts, as opposed to the many
# annual/monthly/one_off crypto series (price targets, ATH bets, etc.) that
# aren't "interval markets" in the sense the spec means.
CRYPTO_INTERVAL_FREQUENCIES = {"fifteen_min", "hourly", "daily"}

# Major-league per-game series, hand-mapped to the-odds-api's sport keys so
# sports_ai.py can fetch exactly the right odds without fuzzy-guessing a
# league from market text. Kalshi runs ~3000 sports series (one per
# tournament/prop-bet type); querying all of them every cycle isn't
# reasonable, so this is deliberately a curated list of straight
# team-vs-team moneyline-style series for the leagues the-odds-api covers.
SPORTS_SERIES_TO_ODDS_KEY = {
    "KXNFLGAME": "americanfootball_nfl",
    "KXNBAGAME": "basketball_nba",
    "KXWNBAGAME": "basketball_wnba",
    "KXNHLGAME": "icehockey_nhl",
    "KXMLBGAME": "baseball_mlb",
    "KXNCAAFGAME": "americanfootball_ncaaf",
    "KXEPLGAME": "soccer_epl",
}

SERIES_CACHE_TTL_SECONDS = 3600

# Series `tags` are inconsistently populated on Kalshi's side (many interval
# series - KXSOL, KXLTC, KXAVAX, KXLINK, KXDOT, KXBCH, KXRIPPLE... - carry no
# coin tag at all), so asset is detected from the series ticker text itself.
# Order matters only where one symbol could be a substring of another; none
# of these collide within Kalshi's actual crypto interval tickers.
ASSET_TICKER_PATTERNS = [
    ("BTC", "BTC"),
    ("ETH", "ETH"),
    ("XRP", "XRP"),
    ("RIPPLE", "XRP"),
    ("SOL", "SOL"),
    ("DOGE", "DOGE"),
    ("LTC", "LTC"),
    ("ADA", "ADA"),
    ("AVAX", "AVAX"),
    ("LINK", "LINK"),
    ("DOT", "DOT"),
    ("BNB", "BNB"),
    ("BCH", "BCH"),
    ("XLM", "XLM"),
    ("NEAR", "NEAR"),
    ("TON", "TON"),
    ("ZEC", "ZEC"),
    ("HYPE", "HYPE"),
    ("SHIBA", "SHIB"),
]


def _asset_from_series_ticker(ticker: str) -> str | None:
    upper = ticker.upper()
    for needle, symbol in ASSET_TICKER_PATTERNS:
        if needle in upper:
            return symbol
    return None


class KalshiAuthError(RuntimeError):
    pass


class KalshiAdapter(ExchangeAdapter):
    name = "kalshi"

    def __init__(self, cfg: KalshiConfig):
        self.cfg = cfg
        self._private_key = None
        if cfg.api_key_id:
            try:
                with open(cfg.private_key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(f.read(), password=None)
            except FileNotFoundError:
                logger.warning("Kalshi private key file not found at %s", cfg.private_key_path)
        self.session = requests.Session()
        self._crypto_series_cache: tuple[float, list[dict]] | None = None

    # ---- auth ----

    def _signed_headers(self, method: str, path: str) -> dict:
        if not self._private_key or not self.cfg.api_key_id:
            raise KalshiAuthError(
                "Kalshi credentials missing: set KALSHI_API_KEY_ID and a valid "
                "KALSHI_PRIVATE_KEY_PATH in trading_bot/.env"
            )
        timestamp_ms = str(int(time.time() * 1000))
        message = (timestamp_ms + method.upper() + path).encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.cfg.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        url = self.cfg.base_url.rstrip("/") + path
        for attempt in range(3):
            # Signature covers only the path portion, per Kalshi docs - not query string.
            # Re-signed fresh each attempt since the signature embeds the timestamp.
            headers = self._signed_headers(method, path)
            resp = self.session.request(method, url, headers=headers, params=params, json=json_body, timeout=15)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Kalshi API error {resp.status_code} on {method} {path}: {resp.text[:500]}")
            return resp.json()

    # ---- market data ----
    # Kalshi's real market objects carry no "category"/"series_ticker" field
    # (confirmed against the live API), so category is determined by which
    # series list we queried, not by inspecting the market. Prices come back
    # as decimal-dollar strings (e.g. "0.8900"), not cent integers - every
    # conversion below multiplies by 100.

    @staticmethod
    def _dollars_to_cents(value) -> float:
        try:
            return float(value) * 100.0
        except (TypeError, ValueError):
            return 0.0

    def _to_snapshot(self, m: dict, category: MarketCategory, tag: dict | None = None) -> MarketSnapshot:
        raw = dict(m)
        if tag:
            raw.update(tag)
        last_price = m.get("last_price_dollars")
        return MarketSnapshot(
            ticker=m.get("ticker", ""),
            title=f"{m.get('title', m.get('ticker', ''))} ({m.get('yes_sub_title', '')})".strip(),
            category=category,
            yes_bid=self._dollars_to_cents(m.get("yes_bid_dollars", 0)),
            yes_ask=self._dollars_to_cents(m.get("yes_ask_dollars", 1)),
            no_bid=self._dollars_to_cents(m.get("no_bid_dollars", 0)),
            no_ask=self._dollars_to_cents(m.get("no_ask_dollars", 1)),
            last_price=self._dollars_to_cents(last_price) if last_price is not None else None,
            volume=int(float(m.get("volume_fp", 0) or 0)),
            close_time=m.get("close_time"),
            series_ticker=m.get("event_ticker", "").rsplit("-", 1)[0] if m.get("event_ticker") else None,
            raw=raw,
        )

    def _crypto_series(self) -> list[dict]:
        now = time.time()
        if self._crypto_series_cache and now - self._crypto_series_cache[0] < SERIES_CACHE_TTL_SECONDS:
            return self._crypto_series_cache[1]
        data = self._request("GET", "/series", params={"category": "Crypto"})
        result = []
        for s in data.get("series", []):
            if s.get("frequency") not in CRYPTO_INTERVAL_FREQUENCIES:
                continue
            ticker = s["ticker"]
            # a few "interval"-frequency series aren't actually spot-price bets
            # (e.g. KXSOLDATHOLDINGS tracks a company's treasury, not SOL price)
            if "HOLDINGS" in ticker.upper():
                continue
            asset = _asset_from_series_ticker(ticker)
            if not asset or asset not in COINGECKO_IDS:
                continue
            result.append({"series_ticker": ticker, "asset": asset})
        self._crypto_series_cache = (now, result)
        return result

    def get_markets(self, category: MarketCategory | None = None) -> list[MarketSnapshot]:
        snapshots: list[MarketSnapshot] = []

        if category in (MarketCategory.CRYPTO, None):
            for series in self._crypto_series():
                time.sleep(0.1)  # Kalshi rate-limits; pace requests across ~60 series calls
                try:
                    data = self._request(
                        "GET", "/markets", params={"series_ticker": series["series_ticker"], "status": "open", "limit": 50}
                    )
                except Exception as exc:  # noqa: BLE001 - one bad series shouldn't kill the whole cycle
                    logger.warning("crypto series %s fetch failed: %s", series["series_ticker"], exc)
                    continue
                for m in data.get("markets", []):
                    snapshots.append(self._to_snapshot(m, MarketCategory.CRYPTO, {"_asset": series["asset"]}))

        if category in (MarketCategory.SPORTS, None):
            for series_ticker, sport_key in SPORTS_SERIES_TO_ODDS_KEY.items():
                time.sleep(0.1)
                try:
                    data = self._request(
                        "GET", "/markets", params={"series_ticker": series_ticker, "status": "open", "limit": 50}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("sports series %s fetch failed: %s", series_ticker, exc)
                    continue
                for m in data.get("markets", []):
                    snapshots.append(self._to_snapshot(m, MarketCategory.SPORTS, {"_sport_key": sport_key}))

        return snapshots

    def get_orderbook(self, ticker: str) -> MarketSnapshot:
        data = self._request("GET", f"/markets/{ticker}")
        return self._to_snapshot(data.get("market", data), MarketCategory.OTHER)

    # ---- trading (live mode only; paper mode uses PaperBroker instead) ----

    def place_order(
        self, ticker: str, action: OrderAction, side: Side, contracts: int, price_cents: float
    ) -> Order:
        client_order_id = str(uuid.uuid4())
        body = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": side.value,
            "action": action.value,
            "count": contracts,
            "type": "limit",
            f"{side.value}_price": int(round(price_cents)),
        }
        try:
            data = self._request("POST", "/portfolio/orders", json_body=body)
            order_data = data.get("order", {})
            return Order(
                order_id=order_data.get("order_id", client_order_id),
                ticker=ticker,
                action=action,
                side=side,
                contracts=contracts,
                price_cents=price_cents,
                strategy="",
                status=order_data.get("status", "pending"),
            )
        except Exception as exc:  # noqa: BLE001 - surface as rejected order, don't crash the loop
            logger.error("Kalshi order placement failed: %s", exc)
            return Order(
                order_id=client_order_id,
                ticker=ticker,
                action=action,
                side=side,
                contracts=contracts,
                price_cents=price_cents,
                strategy="",
                status="rejected",
                reason=str(exc),
            )

    def get_positions(self) -> list:
        data = self._request("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    def get_balance_cents(self) -> int:
        data = self._request("GET", "/portfolio/balance")
        return int(data.get("balance", 0))
