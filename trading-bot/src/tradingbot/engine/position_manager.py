"""Active position management: take-profit and stop-loss exits.

Strategies only ever open positions (see the note in settlement.py) --
this is what closes them *before* expiry based on price movement, rather
than passively waiting for the underlying Kalshi market to settle. Runs
every cycle, before the kill switch check: closing risk is not a "new
trade" the kill switch is meant to block (the kill command's own
--flatten flag makes the same distinction).

Reads positions from adapter.get_account_state() rather than the local
state store directly. In paper mode that account state is itself backed
by the store, so behavior is unchanged -- but in live mode it's Kalshi's
real account/portfolio, which real orders never write into the local
store. Reading through the adapter is what lets this same exit logic
place real sell orders against a real live position instead of only
ever working against simulated paper fills.
"""
from __future__ import annotations

from ..adapters.base import ExchangeAdapter
from ..logging_setup import BotLogs
from ..models import OrderAction, OrderRequest


def check_exits(adapter: ExchangeAdapter, logs: BotLogs, cycle_id: str,
                 take_profit_pct: float, stop_loss_pct: float) -> int:
    """Close any open position that has moved take_profit_pct in its favor
    or stop_loss_pct against it, relative to its average entry price.
    Returns the number of positions closed."""
    exited = 0
    for position in adapter.get_account_state().positions:
        if position.quantity <= 0 or position.avg_price <= 0:
            continue

        market = adapter.get_market(position.ticker)
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

        # Mark-to-market at the price you could actually sell at right now.
        current_bid = market.yes_bid if position.side.value == "yes" else market.no_bid
        pct_change = (current_bid - position.avg_price) / position.avg_price

        if pct_change >= take_profit_pct:
            reason = f"take-profit: price up {pct_change:+.1%} from entry (target {take_profit_pct:.0%})"
        elif pct_change <= -stop_loss_pct:
            reason = f"stop-loss: price down {pct_change:+.1%} from entry (limit -{stop_loss_pct:.0%})"
        else:
            continue

        order = OrderRequest(ticker=position.ticker, side=position.side, action=OrderAction.SELL,
                              count=position.quantity, limit_price=current_bid, strategy_name=position.strategy_name)
        result = adapter.place_order(order)

        logs.log_order(
            event_type="exit_order", strategy_name=position.strategy_name, ticker=position.ticker,
            side=position.side.value, action="sell", count=position.quantity, price=current_bid,
            status=result.status.value, reason=reason, exchange_order_id=result.exchange_order_id,
        )
        logs.log_decision(
            cycle_id=cycle_id, strategy_name=position.strategy_name, ticker=position.ticker, action="sell",
            size_usd=position.quantity * current_bid, confidence=1.0, edge=abs(pct_change), reasoning=reason,
            inputs={"avg_price": position.avg_price, "current_bid": current_bid, "pct_change": pct_change},
            traded=result.status.value in ("filled", "partially_filled", "pending"),
        )
        logs.logger.info(f"[cycle {cycle_id}] exit: {position.ticker} ({position.side.value}) closed -- {reason}")
        exited += 1
    return exited
