"""Settle expired Kalshi positions.

Strategies only ever emit BUY signals -- there is no sell/exit logic
anywhere in the strategy layer. Kalshi's binary markets resolve on their
own at close time (the winning side pays $1.00/contract, the losing side
$0.00), but without this module nothing would ever notice: positions would
sit "open" forever in the local state store, balance would never reflect
the payout, and realized PnL (and any win-rate reporting built on it)
would never move even after the real market had actually settled.

Called once per poll cycle, before strategies evaluate, so freed-up
exposure from newly-settled positions is available for that cycle's
trades.
"""
from __future__ import annotations

from ..adapters.base import ExchangeAdapter
from ..logging_setup import BotLogs
from ..state_store import StateStore


def settle_expired_positions(adapter: ExchangeAdapter, store: StateStore, logs: BotLogs) -> int:
    """Check every open position's market for a final result and realize
    PnL for any that have settled. Returns the number of positions settled."""
    settled_count = 0
    for row in store.get_positions():
        ticker, side, strategy_name = row["ticker"], row["side"], row["strategy_name"]
        quantity, avg_price = row["quantity"], row["avg_price"]

        market = adapter.get_market(ticker)
        if market is None:
            continue

        result = (market.raw or {}).get("result", "")
        if result not in ("yes", "no"):
            continue  # not settled yet

        settlement_value = 1.0 if result == side else 0.0
        realized_pnl = (settlement_value - avg_price) * quantity

        store.upsert_position(ticker, side, strategy_name, -quantity, settlement_value)
        store.adjust_balance(settlement_value * quantity)
        store.add_realized_pnl(realized_pnl)

        logs.log_order(
            event_type="settlement", strategy_name=strategy_name, ticker=ticker, side=side,
            action="settle", count=quantity, price=settlement_value, status="settled",
            reason=f"market resolved to '{result}'",
        )
        logs.logger.info(
            f"[settlement] {ticker} ({side}) resolved to '{result}': "
            f"realized_pnl={realized_pnl:+.2f} on {quantity} contract(s)"
        )
        settled_count += 1
    return settled_count
