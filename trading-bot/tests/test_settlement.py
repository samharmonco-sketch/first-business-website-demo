from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.engine.settlement import settle_expired_positions
from tradingbot.logging_setup import BotLogs
from tradingbot.models import Market, OrderAction, OrderRequest, Side
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


def make_market(ticker, result=""):
    return Market(ticker=ticker, series_ticker="KXBTC", title="test", yes_bid=0.5, yes_ask=0.52,
                  no_bid=0.48, no_ask=0.5, volume=10, close_ts=9999999999, raw={"result": result})


def make_store(tmp_path):
    return StateStore(tmp_path / "state.db", starting_bankroll_usd=1000.0)


def make_logs(tmp_path):
    return BotLogs(tmp_path / "logs", "decisions.jsonl", "orders.jsonl", "account.jsonl")


def test_settles_winning_yes_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    adapter = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", result="yes")})

    settled = settle_expired_positions(adapter, store, logs)

    assert settled == 1
    assert store.get_positions() == []  # position zeroed out
    # Bought 10 @ 0.40, resolved YES -> pays $1.00/contract -> +$6.00 realized pnl
    assert store.get_daily_pnl() == 6.0
    # Balance: started 1000, no buy simulated here (upsert_position doesn't
    # touch balance), settlement pays out 10 * 1.00 = 10.00
    assert store.get_balance() == 1010.0


def test_settles_losing_yes_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    adapter = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", result="no")})

    settled = settle_expired_positions(adapter, store, logs)

    assert settled == 1
    assert store.get_positions() == []
    # Bought 10 @ 0.40, resolved NO -> YES side pays $0.00 -> -$4.00 realized pnl
    assert store.get_daily_pnl() == -4.0
    assert store.get_balance() == 1000.0  # no payout


def test_does_not_settle_unresolved_market(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "yes", "mispricing", 10, 0.40)
    adapter = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", result="")})

    settled = settle_expired_positions(adapter, store, logs)

    assert settled == 0
    assert len(store.get_positions()) == 1
    assert store.get_daily_pnl() == 0.0


def test_settles_no_side_position(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    store.upsert_position("KXBTC-TEST", "no", "crypto_momentum", 20, 0.60)
    adapter = FakeMarketDataAdapter({"KXBTC-TEST": make_market("KXBTC-TEST", result="no")})

    settled = settle_expired_positions(adapter, store, logs)

    assert settled == 1
    # Bought 20 @ 0.60 on the NO side, market resolved NO -> NO side pays $1.00
    # -> +$8.00 realized pnl
    assert store.get_daily_pnl() == 8.0
