"""End-to-end paper-mode smoke test: proves poll -> evaluate -> risk-gate ->
order -> log works across a full cycle using a mock market data source (this
sandbox's network policy blocks live calls to Kalshi, so this substitutes a
deterministic fake data feed rather than skipping the integration test)."""
from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.adapters.paper import PaperTradingAdapter
from tradingbot.config import (
    AnthropicConfig, AppConfig, BankrollConfig, DayOneConfig, ExchangeConfig,
    ExitsConfig, LoggingConfig, OddsApiConfig, PollingConfig, RiskConfig,
)
from tradingbot.engine.execution import ExecutionEngine
from tradingbot.logging_setup import BotLogs
from tradingbot.models import Market
from tradingbot.risk.manager import RiskManager
from tradingbot.state_store import StateStore
from tradingbot.strategies.registry import build_strategies


class MockMarketDataAdapter(ExchangeAdapter):
    name = "mock"

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


def make_config(tmp_path):
    exchange = ExchangeConfig(name="kalshi", use_demo=True, base_url="", demo_base_url="", request_timeout_seconds=5)
    polling = PollingConfig(interval_seconds=1, crypto_series_tickers=["KXBTC"], sports_series_tickers=[], max_markets_per_cycle=10)
    bankroll = BankrollConfig(
        starting_bankroll_usd=1500.0, max_position_usd=50.0, max_position_pct_of_bankroll=0.05,
        max_total_exposure_usd=300.0, daily_loss_limit_usd=100.0, per_market_exposure_usd=75.0,
        per_strategy_exposure_usd=150.0, min_edge_to_trade=0.05, min_confidence_to_trade=0.55,
    )
    day_one = DayOneConfig(enabled=True, min_edge_to_trade=0.02, min_confidence_to_trade=0.50,
                            max_position_usd=5.0, max_total_exposure_usd=15.0, disable_after_first_trade=True)
    exits = ExitsConfig(take_profit_pct=0.15, stop_loss_pct=0.10)
    risk = RiskConfig(kill_switch_file="KILL_SWITCH", state_db_path="state.db")
    logging_cfg = LoggingConfig(log_dir="logs", level="INFO", decisions_file="decisions.jsonl",
                                 orders_file="orders.jsonl", account_file="account.jsonl")
    anthropic = AnthropicConfig(model="claude-sonnet-5", max_tokens=512)
    strategies_cfg = {
        "enabled": ["dummy_buy"],
        "dummy_buy": {"enabled": True, "size_usd": 3.0},
    }
    return AppConfig(mode="paper", exchange=exchange, polling=polling, bankroll=bankroll,
                      day_one=day_one, strategies=strategies_cfg, exits=exits, risk=risk,
                      logging=logging_cfg, anthropic=anthropic, odds_api=OddsApiConfig(api_key=""),
                      root_dir=tmp_path)


def test_first_run_day_one_produces_a_real_trade(tmp_path):
    """Proves requirement #2: the very first cycle, with a permissive
    day-one strategy against a real (mocked) market snapshot, executes at
    least one small risk-capped trade rather than logging nothing."""
    config = make_config(tmp_path)
    log_dir = config.resolve_path(config.logging.log_dir)
    logs = BotLogs(log_dir, config.logging.decisions_file, config.logging.orders_file, config.logging.account_file)
    store = StateStore(config.resolve_path(config.risk.state_db_path), config.bankroll.starting_bankroll_usd)

    market = Market(ticker="KXBTC-25JUL22", series_ticker="KXBTC", title="BTC up test",
                     yes_bid=0.50, yes_ask=0.52, no_bid=0.48, no_ask=0.50, volume=500, close_ts=9999999999)
    market_data = MockMarketDataAdapter([market])
    adapter = PaperTradingAdapter(market_data, store, logs=logs)

    risk_manager = RiskManager(config.bankroll, config.day_one, config.risk, store, config.root_dir)
    strategies = build_strategies(config.strategies, anthropic_config=config.anthropic, log_dir=log_dir)

    engine = ExecutionEngine(config, adapter, strategies, risk_manager, store, logs)
    summary = engine.run_cycle()

    assert summary["decisions"] >= 1
    assert summary["trades"] >= 1, "day-one mode should produce at least one real trade on first run"

    account = adapter.get_account_state()
    assert len(account.positions) >= 1
    assert account.balance_usd < config.bankroll.starting_bankroll_usd

    decisions = logs.decisions.tail(10)
    assert any(d["traded"] for d in decisions)
    orders = logs.orders.tail(10)
    assert any(o["status"] == "filled" for o in orders)

    # Day-one mode should now be disabled for subsequent cycles.
    assert risk_manager.day_one_active is False


def test_kill_switch_stops_new_trades_mid_run(tmp_path):
    config = make_config(tmp_path)
    log_dir = config.resolve_path(config.logging.log_dir)
    logs = BotLogs(log_dir, config.logging.decisions_file, config.logging.orders_file, config.logging.account_file)
    store = StateStore(config.resolve_path(config.risk.state_db_path), config.bankroll.starting_bankroll_usd)

    market = Market(ticker="KXBTC-25JUL22", series_ticker="KXBTC", title="BTC up test",
                     yes_bid=0.50, yes_ask=0.52, no_bid=0.48, no_ask=0.50, volume=500, close_ts=9999999999)
    adapter = PaperTradingAdapter(MockMarketDataAdapter([market]), store, logs=logs)
    risk_manager = RiskManager(config.bankroll, config.day_one, config.risk, store, config.root_dir)
    strategies = build_strategies(config.strategies, anthropic_config=config.anthropic, log_dir=log_dir)
    engine = ExecutionEngine(config, adapter, strategies, risk_manager, store, logs)

    risk_manager.kill_switch.engage()
    summary = engine.run_cycle()
    assert summary["halted"] is True
    assert summary["trades"] == 0

    account = adapter.get_account_state()
    assert len(account.positions) == 0
