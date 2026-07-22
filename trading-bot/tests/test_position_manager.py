from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.adapters.paper import PaperTradingAdapter
from tradingbot.engine.position_manager import check_exits
from tradingbot.logging_setup import BotLogs
from tradingbot.models import Market
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

    exited = check_exits(adapter, store, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

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

    exited = check_exits(adapter, store, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

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

    exited = check_exits(adapter, store, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 0
    assert len(store.get_positions()) == 1


def test_no_exit_when_no_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    market_data = FakeMarketDataAdapter({})
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    exited = check_exits(adapter, store, logs, "cycle1", take_profit_pct=0.15, stop_loss_pct=0.10)

    assert exited == 0
