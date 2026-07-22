import time

from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.adapters.paper import PaperTradingAdapter
from tradingbot.engine.position_manager import check_exits
from tradingbot.logging_setup import BotLogs
from tradingbot.models import AccountState, Market, OrderResult, OrderStatus, Position, Side
from tradingbot.state_store import StateStore


class FakeMarketDataAdapter(ExchangeAdapter):
    name = "fake"

    def __init__(self, markets_by_ticker):
        self._markets = markets_by_ticker

    def get_markets(self, series_tickers, limit=50):
        return list(self._markets.values())

    def get_market(self, ticker):
        return self._markets.get(ticker)

    def get_account_state(self):
        raise NotImplementedError

    def place_order(self, order):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError


def make_market(ticker, yes_bid, yes_ask, no_bid, no_ask):
    return Market(ticker=ticker, series_ticker="KXBTC", title="test", yes_bid=yes_bid, yes_ask=yes_ask,
                  no_bid=no_bid, no_ask=no_ask, volume=10, close_ts=9999999999)


def make_store(tmp_path):
    return StateStore(tmp_path / "state.db", starting_bankroll_usd=1000.0)


def make_logs(tmp_path):
    return BotLogs(tmp_path / "logs", "decisions.jsonl", "orders.jsonl", "account.jsonl")


def test_take_profit_closes_yes_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    # yes_bid now 0.48 -> +20% from 0.40 entry, above the 15% take-profit target
    market_data = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", 0.48, 0.50, 0.50, 0.52)})
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    exited = check_exits(adapter, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 1
    assert store.get_positions() == []
    # Bought 10 @ 0.40, sold 10 @ 0.48 -> +$0.80 realized pnl
    assert abs(store.get_daily_pnl() - 0.8) < 1e-9


def test_stop_loss_closes_yes_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    # yes_bid now 0.34 -> -15% from 0.40 entry, below the -10% stop-loss limit
    market_data = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", 0.34, 0.36, 0.64, 0.66)})
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    exited = check_exits(adapter, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 1
    assert store.get_positions() == []
    # Bought 10 @ 0.40, sold 10 @ 0.34 -> -$0.60 realized pnl
    assert abs(store.get_daily_pnl() - (-0.6)) < 1e-9


def test_no_exit_within_thresholds(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    # yes_bid 0.42 -> +5%, inside both thresholds
    market_data = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", 0.42, 0.44, 0.56, 0.58)})
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    exited = check_exits(adapter, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 0
    assert len(store.get_positions()) == 1


def test_no_exit_when_no_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    market_data = FakeMarketDataAdapter({})
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    exited = check_exits(adapter, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 0


def test_no_exit_on_closed_market_with_zero_bid(tmp_path):
    """Regression test: a market past its close_time but not yet settled
    has no live order book -- Kalshi returns yes_bid=0/no_bid=0 for it.
    That must NOT be read as a real -100% stop-loss (a real production
    incident: two positions both "exited" at exactly -100% the instant
    their markets closed, wiping most of the paper bankroll on a data
    artifact rather than an actual price move)."""
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    closed_market = Market(ticker="KXBTC-TEST", series_ticker="KXBTC", title="test",
                            yes_bid=0.0, yes_ask=0.0, no_bid=0.0, no_ask=0.0,
                            volume=10, close_ts=time.time() - 60, status="closed")
    market_data = FakeMarketDataAdapter({"KXBTC-TEST": closed_market})
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    exited = check_exits(adapter, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 0
    assert len(store.get_positions()) == 1


class FakeLiveAdapter(ExchangeAdapter):
    """Stands in for the real KalshiAdapter in live mode: positions come
    from the exchange's own account, never from the local state store --
    proves check_exits() can close a position it never wrote itself."""
    name = "fake_live"

    def __init__(self, markets_by_ticker, positions):
        self._markets = markets_by_ticker
        self._positions = positions
        self.placed_orders = []

    def get_markets(self, series_tickers, limit=50):
        return list(self._markets.values())

    def get_market(self, ticker):
        return self._markets.get(ticker)

    def get_account_state(self):
        return AccountState(balance_usd=1000.0, positions=self._positions)

    def place_order(self, order):
        self.placed_orders.append(order)
        return OrderResult(client_order_id=order.client_order_id, exchange_order_id="live-1",
                            status=OrderStatus.FILLED, filled_count=order.count,
                            avg_fill_price=order.limit_price, ticker=order.ticker,
                            side=order.side, action=order.action)

    def cancel_order(self, client_order_id):
        raise NotImplementedError


def test_check_exits_closes_a_position_never_written_to_local_store(tmp_path):
    """Regression test: check_exits() must read positions via
    adapter.get_account_state(), not the local paper-trading store --
    real Kalshi orders (live mode) never touch that store at all, so
    reading it directly would silently exit nothing."""
    logs = make_logs(tmp_path)
    position = Position(ticker="KXBTC-TEST", side=Side.YES, quantity=10, avg_price=0.40, strategy_name="mispricing")
    market_data = {"KXBTC-TEST": make_market("KXBTC-TEST", 0.48, 0.50, 0.50, 0.52)}
    adapter = FakeLiveAdapter(market_data, [position])

    exited = check_exits(adapter, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 1
    assert len(adapter.placed_orders) == 1
    assert adapter.placed_orders[0].ticker == "KXBTC-TEST"
