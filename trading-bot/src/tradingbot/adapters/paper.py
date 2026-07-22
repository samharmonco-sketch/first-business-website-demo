"""Paper trading adapter: simulated fills against live market data.

Wraps a real market-data-capable adapter (e.g. KalshiAdapter, whose market
data endpoints are public and unauthenticated) for get_markets(), but never
calls any authenticated/trading endpoint. All fills, positions, and balance
are simulated and persisted in the local StateStore. This is the default
mode -- live trading must be explicitly enabled in config.
"""
from __future__ import annotations

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
from ..state_store import StateStore
from .base import ExchangeAdapter


class PaperTradingAdapter(ExchangeAdapter):
    name = "paper"

    def __init__(self, market_data_source: ExchangeAdapter, state_store: StateStore, logs: BotLogs | None = None):
        self._market_data_source = market_data_source
        self._store = state_store
        self.logs = logs

    def get_markets(self, series_tickers: list[str], limit: int = 50) -> list[Market]:
        return self._market_data_source.get_markets(series_tickers, limit=limit)

    def get_market(self, ticker: str) -> Market | None:
        return self._market_data_source.get_market(ticker)

    def get_account_state(self) -> AccountState:
        balance = self._store.get_balance()
        positions = [
            Position(
                ticker=row["ticker"], side=Side(row["side"]), quantity=row["quantity"],
                avg_price=row["avg_price"], strategy_name=row["strategy_name"],
            )
            for row in self._store.get_positions()
        ]
        total_exposure = sum(p.cost_basis_usd for p in positions)
        return AccountState(
            balance_usd=balance,
            positions=positions,
            realized_pnl_today_usd=self._store.get_daily_pnl(),
            total_exposure_usd=total_exposure,
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Simulate an immediate fill at the requested limit price (i.e.
        assume the order crosses the spread it was priced against -- the
        strategy is expected to have priced against the live ask/bid)."""
        cost_usd = order.count * order.limit_price
        balance = self._store.get_balance()

        if order.action == OrderAction.BUY and cost_usd > balance:
            result = OrderResult(
                client_order_id=order.client_order_id, exchange_order_id=None,
                status=OrderStatus.REJECTED, filled_count=0, avg_fill_price=None,
                ticker=order.ticker, side=order.side, action=order.action,
                reason=f"insufficient paper balance: need ${cost_usd:.2f}, have ${balance:.2f}",
            )
            self._record_order(order, result)
            return result

        realized_pnl_usd = 0.0
        if order.action == OrderAction.SELL:
            existing = self._store.get_position(order.ticker, order.side.value, order.strategy_name)
            avg_cost = existing["avg_price"] if existing else order.limit_price
            realized_pnl_usd = (order.limit_price - avg_cost) * order.count

        qty_delta = order.count if order.action == OrderAction.BUY else -order.count
        self._store.upsert_position(order.ticker, order.side.value, order.strategy_name, qty_delta, order.limit_price)

        if order.action == OrderAction.BUY:
            self._store.adjust_balance(-cost_usd)
        else:
            self._store.adjust_balance(cost_usd)
            self._store.add_realized_pnl(realized_pnl_usd)

        result = OrderResult(
            client_order_id=order.client_order_id,
            exchange_order_id=f"paper-{order.client_order_id[:8]}",
            status=OrderStatus.FILLED,
            filled_count=order.count,
            avg_fill_price=order.limit_price,
            ticker=order.ticker, side=order.side, action=order.action,
            reason="simulated fill (paper mode)",
        )
        self._record_order(order, result)
        return result

    def _record_order(self, order: OrderRequest, result: OrderResult) -> None:
        self._store.record_order(
            client_order_id=order.client_order_id,
            exchange_order_id=result.exchange_order_id,
            ticker=order.ticker, side=order.side.value, action=order.action.value,
            strategy_name=order.strategy_name, count=order.count, limit_price=order.limit_price,
            status=result.status.value, filled_count=result.filled_count,
            avg_fill_price=result.avg_fill_price,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        # All paper orders fill immediately, so there is nothing resting to cancel.
        return False

    def flatten_all_positions(self, account_state: AccountState) -> list[OrderResult]:
        results = []
        for pos in account_state.positions:
            # Sell at current avg price as a conservative paper-mode approximation.
            order = OrderRequest(
                ticker=pos.ticker, side=pos.side, action=OrderAction.SELL,
                count=pos.quantity, limit_price=pos.avg_price, strategy_name=pos.strategy_name,
            )
            results.append(self.place_order(order))
        return results
