"""Active position management: take-profit and stop-loss exits.

Strategies only ever open positions (see the note in settlement.py) --
this is what closes them *before* expiry based on price movement, rather
than passively waiting for the underlying Kalshi market to settle. Runs
every cycle, before the kill switch check: closing risk is not a "new
trade" the kill switch is meant to block (the kill command's own
--flatten flag makes the same distinction).
"""
from __future__ import annotations

from ..adapters.base import ExchangeAdapter
from ..logging_setup import BotLogs
from ..models import OrderAction, OrderRequest, Side
from ..state_store import StateStore


def check_exits(adapter: ExchangeAdapter, store: StateStore, logs: BotLogs, cycle_id: str,
                 take_profit_pct: float, stop_loss_pct: float) -> int:
    """Close any open position that has moved take_profit_pct in its favor
    or stop_loss_pct against it, relative to its average entry price.
    Returns the number of positions closed."""
    exited = 0
    for row in store.get_positions():
        ticker, side_str, strategy_name = row["ticker"], row["side"], row["strategy_name"]
        quantity, avg_price = row["quantity"], row["avg_price"]
        if quantity <= 0 or avg_price <= 0:
            continue

        market = adapter.get_market(ticker)
        if market is None:
            continue
        if market.status != "active":
            # Trading has halted on this market (it's past its close_time,
            # awaiting settlement) -- there is no live order book anymore,
            # so Kalshi returns 0 for yes_bid/no_bid. That 0 is an absent
            # quote, not a real price crash: mark-to-market against it
            # would read as an instant, fake -100% stop-loss on a position
            # that hasn't actually been determined to be a loser yet.
            # settle_expired_positions() (runs before this, each cycle)
            # is what correctly closes it once Kalshi posts a real result.
            continue

        side = Side(side_str)
        # Mark-to-market at the price you could actually sell at right now.
        current_bid = market.yes_bid if side == Side.YES else market.no_bid
        pct_change = (current_bid - avg_price) / avg_price

        if pct_change >= take_profit_pct:
            reason = f"take-profit: price up {pct_change:+.1%} from entry (target {take_profit_pct:.0%})"
        elif pct_change <= -stop_loss_pct:
            reason = f"stop-loss: price down {pct_change:+.1%} from entry (limit -{stop_loss_pct:.0%})"
        else:
            continue

        order = OrderRequest(ticker=ticker, side=side, action=OrderAction.SELL,
                              count=quantity, limit_price=current_bid, strategy_name=strategy_name)
        result = adapter.place_order(order)

        logs.log_order(
            event_type="exit_order", strategy_name=strategy_name, ticker=ticker, side=side.value,
            action="sell", count=quantity, price=current_bid, status=result.status.value,
            reason=reason, exchange_order_id=result.exchange_order_id,
        )
        logs.log_decision(
            cycle_id=cycle_id, strategy_name=strategy_name, ticker=ticker, action="sell",
            size_usd=quantity * current_bid, confidence=1.0, edge=abs(pct_change), reasoning=reason,
            inputs={"avg_price": avg_price, "current_bid": current_bid, "pct_change": pct_change},
            traded=result.status.value in ("filled", "partially_filled", "pending"),
        )
        logs.logger.info(f"[cycle {cycle_id}] exit: {ticker} ({side.value}) closed -- {reason}")
        exited += 1
    return exited
