#!/usr/bin/env python3
"""Standalone validation: confirms RSA-PSS signed auth works and market data
parses correctly against Kalshi's DEMO/SANDBOX API. Read-only.

This script has NO code path that calls place_order/cancel_order -- it only
exercises get_markets() (public, unauthenticated) and get_account_state()
(authenticated, read-only: GET /portfolio/balance + GET /portfolio/positions).
It cannot place a trade even by accident. Also hardcodes use_demo=True
regardless of your .env, so it can never hit production Kalshi.

Run from the trading-bot/ directory:
    source .venv/bin/activate
    python scripts/validate_kalshi_demo.py

Requires in your environment (.env or exported):
    KALSHI_API_KEY_ID
    KALSHI_PRIVATE_KEY_PATH   (path to the RSA PRIVATE key file you generated
                                and whose PUBLIC key you uploaded to Kalshi)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

from tradingbot.adapters.kalshi import KalshiAdapter, KalshiAuthError  # noqa: E402
from tradingbot.config import ExchangeConfig  # noqa: E402


def main() -> int:
    api_key_id = os.environ.get("KALSHI_API_KEY_ID", "")
    private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")

    print("=" * 70)
    print(" KALSHI DEMO VALIDATION (read-only, no orders will be placed)")
    print("=" * 70)
    print(f" API Key ID: {api_key_id or '(not set)'}")
    print(f" Private key path: {private_key_path or '(not set)'}")
    print()

    exchange_config = ExchangeConfig(
        name="kalshi", use_demo=True,  # hardcoded -- this script never touches production
        base_url="https://api.elections.kalshi.com/trade-api/v2",
        demo_base_url="https://demo-api.kalshi.co/trade-api/v2",
        request_timeout_seconds=15,
        api_key_id=api_key_id, private_key_path=private_key_path,
    )
    adapter = KalshiAdapter(exchange_config)

    overall_ok = True

    # --- Step 1: public market data ---
    print("[1/2] Fetching public market data (GET /markets, no auth)...")
    try:
        markets = adapter.get_markets(["KXBTC", "KXETH"], limit=5)
        if not markets:
            print("  WARN: request succeeded but returned zero markets. Either")
            print("        there are no open KXBTC/KXETH markets right now, or")
            print("        the series tickers are wrong -- check Kalshi's site.")
        else:
            print(f"  PASS: got {len(markets)} market(s). Sample:")
            for m in markets[:3]:
                close_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(m.close_ts))
                print(f"    {m.ticker:<28} '{m.title[:40]}'")
                print(f"      yes_bid={m.yes_bid:.2f} yes_ask={m.yes_ask:.2f} "
                      f"no_bid={m.no_bid:.2f} no_ask={m.no_ask:.2f} closes={close_str}")
    except Exception as exc:
        overall_ok = False
        print(f"  FAIL: {exc}")
        print("        This is public data -- a failure here means either a network")
        print("        problem, a Kalshi outage, or the market-parsing code (adapters/")
        print("        kalshi.py:_parse_market) doesn't match Kalshi's actual response shape.")
    print()

    # --- Step 2: authenticated account state ---
    print("[2/2] Fetching account state (GET /portfolio/balance + /portfolio/positions, signed)...")
    if not api_key_id or not private_key_path:
        print("  SKIP: KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH not set.")
    else:
        try:
            account = adapter.get_account_state()
            print(f"  PASS: balance=${account.balance_usd:.2f}, positions={len(account.positions)}")
            for p in account.positions[:5]:
                print(f"    {p.ticker} {p.side.value} qty={p.quantity} avg_price={p.avg_price:.2f}")
        except KalshiAuthError as exc:
            overall_ok = False
            print(f"  FAIL (auth not configured): {exc}")
        except Exception as exc:
            overall_ok = False
            print(f"  FAIL: {exc}")
            print("        If this is a 401/403, the RSA-PSS signing is likely wrong --")
            print("        check adapters/kalshi.py:_auth_headers against Kalshi's current")
            print("        docs (timestamp in ms, path must exclude query string but include")
            print("        the /trade-api/v2 prefix, PSS salt length == digest size).")
            print("        If it's a network/connection error, check your outbound access")
            print("        to demo-api.kalshi.co from this machine.")
    print()

    print("=" * 70)
    print(" RESULT: " + ("PASS" if overall_ok else "FAIL -- see errors above"))
    print("=" * 70)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
