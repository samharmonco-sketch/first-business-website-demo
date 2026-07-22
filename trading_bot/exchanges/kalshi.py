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
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import KalshiConfig
from ..models import MarketCategory, MarketSnapshot, Order, OrderAction, Side
from .base import ExchangeAdapter

logger = logging.getLogger("trading_bot.kalshi")

CRYPTO_KEYWORDS = ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol")
SPORTS_KEYWORDS = ("nfl", "nba", "nhl", "mlb", "soccer", "sports", "ncaa", "epl", "ufc")


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
        # Signature covers only the path portion, per Kalshi docs - not query string.
        headers = self._signed_headers(method, path)
        resp = self.session.request(method, url, headers=headers, params=params, json=json_body, timeout=15)
        if resp.status_code >= 400:
            raise RuntimeError(f"Kalshi API error {resp.status_code} on {method} {path}: {resp.text[:500]}")
        return resp.json()

    # ---- market data ----

    def _category_of(self, market: dict) -> MarketCategory:
        text = " ".join(
            str(market.get(k, "")) for k in ("category", "title", "ticker", "series_ticker")
        ).lower()
        if any(kw in text for kw in CRYPTO_KEYWORDS):
            return MarketCategory.CRYPTO
        if any(kw in text for kw in SPORTS_KEYWORDS):
            return MarketCategory.SPORTS
        return MarketCategory.OTHER

    def _to_snapshot(self, m: dict) -> MarketSnapshot:
        return MarketSnapshot(
            ticker=m.get("ticker", ""),
            title=m.get("title", m.get("ticker", "")),
            category=self._category_of(m),
            yes_bid=float(m.get("yes_bid", 0) or 0),
            yes_ask=float(m.get("yes_ask", 100) or 100),
            no_bid=float(m.get("no_bid", 0) or 0),
            no_ask=float(m.get("no_ask", 100) or 100),
            last_price=m.get("last_price"),
            volume=int(m.get("volume", 0) or 0),
            close_time=m.get("close_time"),
            series_ticker=m.get("series_ticker"),
            raw=m,
        )

    def get_markets(self, category: MarketCategory | None = None) -> list[MarketSnapshot]:
        params = {"status": "open", "limit": 200}
        data = self._request("GET", "/markets", params=params)
        markets = data.get("markets", [])
        snapshots = [self._to_snapshot(m) for m in markets]
        if category is not None:
            snapshots = [s for s in snapshots if s.category == category]
        return snapshots

    def get_orderbook(self, ticker: str) -> MarketSnapshot:
        data = self._request("GET", f"/markets/{ticker}")
        return self._to_snapshot(data.get("market", data))

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
