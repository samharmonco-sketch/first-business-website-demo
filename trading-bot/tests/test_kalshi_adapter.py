"""Regression test for KalshiAdapter._parse_market using a real response
captured from Kalshi's demo API (GET /markets?series_ticker=KXBTC). This
caught a real bug: the adapter originally assumed cents-integer yes_bid/
yes_ask fields that don't exist in Kalshi's actual response -- the real
fields are yes_bid_dollars/yes_ask_dollars (decimal strings, already 0-1)."""
import time

from tradingbot.adapters.kalshi import KalshiAdapter

REAL_KALSHI_MARKET_SAMPLE = {
    "can_close_early": True,
    "close_time": "2026-07-23T05:00:00Z",
    "created_time": "2026-07-22T09:36:18.333738Z",
    "event_ticker": "KXBTC-26JUL2301",
    "exchange_index": 0,
    "expected_expiration_time": "2026-07-23T05:05:00Z",
    "expiration_time": "2026-07-30T05:00:00Z",
    "expiration_value": "",
    "floor_strike": 74799.99,
    "last_price_dollars": "0.0000",
    "latest_expiration_time": "2026-07-30T05:00:00Z",
    "liquidity_dollars": "0.0000",
    "market_type": "binary",
    "no_ask_dollars": "1.0000",
    "no_bid_dollars": "1.0000",
    "no_sub_title": "$74,800 or above",
    "notional_value_dollars": "1.0000",
    "open_interest_fp": "0.00",
    "open_time": "2026-07-22T10:04:28Z",
    "previous_price_dollars": "0.0000",
    "previous_yes_ask_dollars": "0.0000",
    "previous_yes_bid_dollars": "0.0000",
    "price_level_structure": "linear_cent",
    "result": "",
    "settlement_timer_seconds": 60,
    "status": "active",
    "strike_type": "greater",
    "subtitle": "$74,800 or above",
    "ticker": "KXBTC-26JUL2301-T74799.99",
    "title": "Bitcoin price range on Jul 23, 2026?",
    "updated_time": "2026-07-22T10:04:30.575429Z",
    "volume_24h_fp": "0.00",
    "volume_fp": "0.00",
    "yes_ask_dollars": "0.5200",
    "yes_ask_size_fp": "10.00",
    "yes_bid_dollars": "0.4800",
    "yes_bid_size_fp": "10.00",
    "yes_sub_title": "$74,800 or above",
}


def test_parse_market_reads_real_kalshi_field_names():
    market = KalshiAdapter._parse_market(REAL_KALSHI_MARKET_SAMPLE, "KXBTC")

    assert market.ticker == "KXBTC-26JUL2301-T74799.99"
    assert market.title == "Bitcoin price range on Jul 23, 2026?"
    assert market.yes_bid == 0.48
    assert market.yes_ask == 0.52
    assert market.no_bid == 1.00
    assert market.no_ask == 1.00
    assert market.volume == 0.0
    assert market.raw["floor_strike"] == 74799.99
    assert market.raw["yes_sub_title"] == "$74,800 or above"
    # close_time "2026-07-23T05:00:00Z" -> should parse to a real future timestamp
    assert market.close_ts > time.time()


def test_parse_market_defaults_when_no_liquidity():
    """The exact response the user captured -- a thin/new market with zero
    bids -- must not crash and must reflect the real zero values, not the
    old cents-based defaults (0.00/1.00 was a symptom of the bug, not
    evidence the market really has no liquidity in every case)."""
    sample = dict(REAL_KALSHI_MARKET_SAMPLE)
    sample["yes_bid_dollars"] = "0.0000"
    sample["yes_ask_dollars"] = "0.0000"
    sample["no_bid_dollars"] = "0.0000"
    sample["no_ask_dollars"] = "0.0000"

    market = KalshiAdapter._parse_market(sample, "KXBTC")
    assert market.yes_bid == 0.0
    assert market.yes_ask == 0.0
    assert market.no_bid == 0.0
    assert market.no_ask == 0.0
