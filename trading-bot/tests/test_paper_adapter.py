import pytest

from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.adapters.paper import PaperTradingAdapter
from tradingbot.models import Market, OrderAction, OrderRequest, OrderStatus, Side
from tradingbot.state_store import StateStore


class FakeMarketDataAdapter(ExchangeAdapter):
    name = "fake"

    def __init__(self, markets):
        self._markets = markets

    def get_markets(self, series_tickers, limit=50):
        return self._markets

    def get_market(self, ticker):
        return next((m for m in self._markets if m.ticker == ticker), None)

    def get_account_state(self):
        raise NotImplementedError

    def place_order(self, order):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError


def make_market():
    return Market(ticker="KXBTC-TEST", series_ticker="KXBTC", title="test", yes_bid=0.5,
                  yes_ask=0.52, no_bid=0.48, no_ask=0.5, volume=10, close_ts=9999999999)


def test_paper_buy_fills_and_updates_balance(tmp_path):
    store = StateStore(tmp_path / "state.db", starting_bankroll_usd=100.0)
    adapter = PaperTradingAdapter(FakeMarketDataAdapter([make_market()]), store)

    order = OrderRequest(ticker="KXBTC-TEST", side=Side.YES, action=OrderAction.BUY,
                          count=10, limit_price=0.52, strategy_name="test_strategy")
    result = adapter.place_order(order)

    assert result.status == OrderStatus.FILLED
    assert result.filled_count == 10

    account = adapter.get_account_state()
    assert account.balance_usd == 100.0 - 10 * 0.52
    assert len(account.positions) == 1
    assert account.positions[0].quantity == 10


def test_paper_buy_rejected_when_insufficient_balance(tmp_path):
    store = StateStore(tmp_path / "state.db", starting_bankroll_usd=1.0)
    adapter = PaperTradingAdapter(FakeMarketDataAdapter([make_market()]), store)

    order = OrderRequest(ticker="KXBTC-TEST", side=Side.YES, action=OrderAction.BUY,
                          count=10, limit_price=0.52, strategy_name="test_strategy")
    result = adapter.place_order(order)

    assert result.status == OrderStatus.REJECTED
    account = adapter.get_account_state()
    assert account.balance_usd == 1.0
    assert len(account.positions) == 0


def test_paper_sell_realizes_pnl(tmp_path):
    store = StateStore(tmp_path / "state.db", starting_bankroll_usd=100.0)
    adapter = PaperTradingAdapter(FakeMarketDataAdapter([make_market()]), store)

    buy = OrderRequest(ticker="KXBTC-TEST", side=Side.YES, action=OrderAction.BUY,
                        count=10, limit_price=0.50, strategy_name="test_strategy")
    adapter.place_order(buy)

    sell = OrderRequest(ticker="KXBTC-TEST", side=Side.YES, action=OrderAction.SELL,
                         count=10, limit_price=0.60, strategy_name="test_strategy")
    result = adapter.place_order(sell)

    assert result.status == OrderStatus.FILLED
    # Bought 10 @ $0.50, sold 10 @ $0.60 -> realized PnL is the $0.10/contract
    # profit, not the full $6.00 in sale proceeds.
    assert store.get_daily_pnl() == pytest.approx(1.0)
