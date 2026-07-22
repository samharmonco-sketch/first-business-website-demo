"""Kalshi exchange adapter.

Auth: Kalshi's v2 trading API requires RSA-PSS signed requests for every
*authenticated* endpoint (placing orders, reading your balance/positions).
Market data endpoints (GET /markets, GET /markets/{ticker}/orderbook) are
public and require no signing, which is what lets paper mode run against
live market data with zero API keys configured.

Signing scheme (per Kalshi's published docs as of writing):
  message   = f"{timestamp_ms}{METHOD}{path}"   (path excludes query string,
              includes the /trade-api/v2 prefix)
  signature = base64(RSASSA-PSS-SHA256(private_key, message))
  headers   = {
      "KALSHI-ACCESS-KEY": api_key_id,
      "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
      "KALSHI-ACCESS-SIGNATURE": signature,
  }

IMPORTANT: verify this against Kalshi's current live API reference before
enabling live trading -- exchange APIs change, and this adapter was written
without the ability to hit Kalshi's servers directly to confirm wire format
(this sandbox's network policy blocks it). Confirm end-to-end against the
Kalshi demo/sandbox environment (use_demo: true) before flipping to real
production credentials.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import ExchangeConfig
from ..logging_setup import BotLogs
from ..models import (
    AccountState,
    Market,
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    Position,
    Side,
)
from .base import ExchangeAdapter


class KalshiAuthError(RuntimeError):
    pass


class KalshiAdapter(ExchangeAdapter):
    name = "kalshi"

    def __init__(self, config: ExchangeConfig, logs: BotLogs | None = None):
        self.config = config
        self.logs = logs
        self.base_url = config.active_base_url.rstrip("/")
        self._private_key = None
        if config.private_key_path:
            key_path = Path(config.private_key_path)
            if key_path.exists():
                with open(key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(f.read(), password=None)

    # ---------------------------------------------------------------
    # Signing / low-level HTTP
    # ---------------------------------------------------------------
    def _auth_headers(self, method: str, path: str) -> dict:
        if self._private_key is None or not self.config.api_key_id:
            raise KalshiAuthError(
                "Kalshi credentials not configured (KALSHI_API_KEY_ID / "
                "KALSHI_PRIVATE_KEY_PATH). Cannot call authenticated endpoints."
            )
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method}{path}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }

    def _request(self, method: str, path: str, *, params: dict | None = None,
                  json_body: dict | None = None, authenticated: bool = False) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers.update(self._auth_headers(method, f"/trade-api/v2{path}"))
        resp = requests.request(
            method, url, params=params, json=json_body, headers=headers,
            timeout=self.config.request_timeout_seconds,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Kalshi API error {resp.status_code} on {method} {path}: {resp.text[:500]}")
        return resp.json()

    # ---------------------------------------------------------------
    # Public market data (no auth required)
    # ---------------------------------------------------------------
    def get_markets(self, series_tickers: list[str], limit: int = 50) -> list[Market]:
        out: list[Market] = []
        for series_ticker in series_tickers:
            data = self._request(
                "GET", "/markets",
                params={"series_ticker": series_ticker, "status": "open", "limit": limit},
            )
            for m in data.get("markets", []):
                out.append(self._parse_market(m, series_ticker))
        return out

    def get_market(self, ticker: str) -> Market | None:
        try:
            data = self._request("GET", f"/markets/{ticker}")
        except RuntimeError:
            return None
        m = data.get("market")
        if not m:
            return None
        return self._parse_market(m, m.get("series_ticker", ""))

    @staticmethod
    def _parse_market(m: dict, series_ticker: str) -> Market:
        # Kalshi's real /markets response quotes yes/no bid/ask as decimal-
        # dollar strings (e.g. "yes_ask_dollars": "0.0000"), already in the
        # 0-1 range -- confirmed against a live demo response. There is no
        # cents-integer "yes_bid"/"yes_ask" field; volume is "volume_fp".
        return Market(
            ticker=m["ticker"],
            series_ticker=series_ticker or m.get("event_ticker", ""),
            title=m.get("title", m["ticker"]),
            yes_bid=float(m.get("yes_bid_dollars") or 0),
            yes_ask=float(m.get("yes_ask_dollars") or 1),
            no_bid=float(m.get("no_bid_dollars") or 0),
            no_ask=float(m.get("no_ask_dollars") or 1),
            volume=float(m.get("volume_fp") or 0),
            close_ts=_parse_close_time(m.get("close_time")),
            status=m.get("status", "unknown"),
            raw=m,
        )

    def get_orderbook(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}/orderbook")

    # ---------------------------------------------------------------
    # Authenticated: account state
    # ---------------------------------------------------------------
    def get_account_state(self) -> AccountState:
        balance_data = self._request("GET", "/portfolio/balance", authenticated=True)
        positions_data = self._request("GET", "/portfolio/positions", authenticated=True)
        orders_data = self._request("GET", "/portfolio/orders", params={"status": "resting"}, authenticated=True)

        positions = []
        for p in positions_data.get("market_positions", []):
            qty = p.get("position", 0)
            if qty == 0:
                continue
            positions.append(Position(
                ticker=p["ticker"],
                side=Side.YES if qty > 0 else Side.NO,
                quantity=abs(qty),
                avg_price=abs(p.get("market_exposure", 0)) / (100.0 * max(abs(qty), 1)),
                strategy_name=p.get("ticker", ""),
            ))

        balance_usd = balance_data.get("balance", 0) / 100.0
        total_exposure = sum(pos.cost_basis_usd for pos in positions)
        return AccountState(balance_usd=balance_usd, positions=positions, total_exposure_usd=total_exposure)

    # ---------------------------------------------------------------
    # Authenticated: trading
    # ---------------------------------------------------------------
    def place_order(self, order: OrderRequest) -> OrderResult:
        price_cents = round(order.limit_price * 100)
        body = {
            "ticker": order.ticker,
            "client_order_id": order.client_order_id,
            "side": order.side.value,
            "action": order.action.value,
            "count": order.count,
            "type": "limit",
            f"{order.side.value}_price": price_cents,
        }
        try:
            data = self._request("POST", "/portfolio/orders", json_body=body, authenticated=True)
        except Exception as exc:
            return OrderResult(
                client_order_id=order.client_order_id, exchange_order_id=None,
                status=OrderStatus.REJECTED, filled_count=0, avg_fill_price=None,
                ticker=order.ticker, side=order.side, action=order.action, reason=str(exc),
            )
        o = data.get("order", {})
        status_map = {
            "resting": OrderStatus.PENDING,
            "executed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELED,
        }
        return OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=o.get("order_id"),
            status=status_map.get(o.get("status"), OrderStatus.PENDING),
            filled_count=o.get("filled_count", 0),
            avg_fill_price=(o.get("yes_price", price_cents) / 100.0),
            ticker=order.ticker, side=order.side, action=order.action, raw=o,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            self._request("DELETE", f"/portfolio/orders/{client_order_id}", authenticated=True)
            return True
        except Exception:
            return False


def _parse_close_time(close_time_str: str | None) -> float:
    if not close_time_str:
        return time.time() + 3600
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return time.time() + 3600
