import time

import tradingbot.dashboard.web as web
from tradingbot.adapters.base import ExchangeAdapter
from tradingbot.config import BankrollConfig, DayOneConfig, RiskConfig
from tradingbot.logging_setup import BotLogs
from tradingbot.models import AccountState
from tradingbot.risk.manager import RiskManager
from tradingbot.state_store import StateStore


class FakeAdapter(ExchangeAdapter):
    def __init__(self, name):
        self.name = name

    def get_markets(self, series_tickers, limit=50):
        return []

    def get_market(self, ticker):
        return None

    def get_account_state(self):
        return AccountState(balance_usd=1500.0, positions=[])

    def place_order(self, order):
        raise NotImplementedError

    def cancel_order(self, client_order_id):
        raise NotImplementedError


def make_logs(tmp_path):
    return BotLogs(tmp_path / "logs", "decisions.jsonl", "orders.jsonl", "account.jsonl")


def make_store(tmp_path):
    return StateStore(tmp_path / "state.db", starting_bankroll_usd=1500.0)


def make_risk_manager(store, tmp_path):
    bankroll = BankrollConfig(starting_bankroll_usd=1500.0, max_position_usd=50.0, max_position_pct_of_bankroll=0.05,
                               max_total_exposure_usd=300.0, daily_loss_limit_usd=100.0, per_market_exposure_usd=75.0,
                               per_strategy_exposure_usd=150.0, min_edge_to_trade=0.05, min_confidence_to_trade=0.55)
    day_one = DayOneConfig(enabled=True, min_edge_to_trade=0.02, min_confidence_to_trade=0.50,
                            max_position_usd=5.0, max_total_exposure_usd=15.0, disable_after_first_trade=True)
    risk_cfg = RiskConfig(kill_switch_file="KILL_SWITCH", state_db_path="state.db")
    return RiskManager(bankroll, day_one, risk_cfg, store, tmp_path)


def log_one_decision(logs):
    logs.log_decision(cycle_id="c1", strategy_name="s", ticker="T", action="hold",
                       size_usd=0.0, confidence=0.0, edge=0.0, reasoning="r", inputs={}, traded=False)


def test_heartbeat_no_cycles_logged_yet_is_red(tmp_path):
    logs = make_logs(tmp_path)
    text, color = web._heartbeat(logs, poll_interval_seconds=60)
    assert "no cycles" in text
    assert color == "#cf222e"


def test_heartbeat_fresh_cycle_is_green(tmp_path):
    logs = make_logs(tmp_path)
    log_one_decision(logs)
    text, color = web._heartbeat(logs, poll_interval_seconds=60)
    assert "ago" in text
    assert color == "#1a7f37"


def test_heartbeat_running_late_is_amber(tmp_path, monkeypatch):
    logs = make_logs(tmp_path)
    log_one_decision(logs)
    real_now = time.time()
    monkeypatch.setattr(web.time, "time", lambda: real_now + 60 * 3)  # 3x the poll interval

    text, color = web._heartbeat(logs, poll_interval_seconds=60)

    assert color == "#9a6700"


def test_heartbeat_long_silence_is_red(tmp_path, monkeypatch):
    logs = make_logs(tmp_path)
    log_one_decision(logs)
    real_now = time.time()
    monkeypatch.setattr(web.time, "time", lambda: real_now + 60 * 10)  # 10x the poll interval -- laptop asleep, etc.

    text, color = web._heartbeat(logs, poll_interval_seconds=60)

    assert color == "#cf222e"


def test_render_html_paper_mode_label(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    risk_manager = make_risk_manager(store, tmp_path)
    adapter = FakeAdapter("paper")

    page = web._render_html(adapter, store, risk_manager, logs, use_demo=True)

    assert "PAPER (simulated fills)" in page


def test_render_html_live_demo_mode_label(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    risk_manager = make_risk_manager(store, tmp_path)
    adapter = FakeAdapter("kalshi")

    page = web._render_html(adapter, store, risk_manager, logs, use_demo=True)

    assert "Kalshi DEMO account" in page
    assert "REAL MONEY" not in page


def test_render_html_live_real_money_mode_label_is_unmistakable(tmp_path):
    """This is the label that matters most -- if use_demo is ever false,
    the dashboard must scream about it rather than looking identical to
    the safe demo mode."""
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    risk_manager = make_risk_manager(store, tmp_path)
    adapter = FakeAdapter("kalshi")

    page = web._render_html(adapter, store, risk_manager, logs, use_demo=False)

    assert "REAL MONEY" in page


def test_render_html_shows_trade_history(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    risk_manager = make_risk_manager(store, tmp_path)
    adapter = FakeAdapter("paper")
    logs.log_order(event_type="order_placed", strategy_name="mispricing", ticker="KXBTC-TEST",
                    side="yes", action="buy", count=10, price=0.42, status="filled")
    logs.log_order(event_type="exit_order", strategy_name="mispricing", ticker="KXBTC-TEST",
                    side="yes", action="sell", count=10, price=0.50, status="filled",
                    reason="take-profit: price up +19.0% from entry (target 15%)")

    page = web._render_html(adapter, store, risk_manager, logs, use_demo=True)

    assert "KXBTC-TEST" in page
    assert "take-profit" in page
    assert "No trades placed yet" not in page


def test_render_html_no_trades_yet(tmp_path):
    store = make_store(tmp_path)
    logs = make_logs(tmp_path)
    risk_manager = make_risk_manager(store, tmp_path)
    adapter = FakeAdapter("paper")

    page = web._render_html(adapter, store, risk_manager, logs, use_demo=True)

    assert "No trades placed yet" in page
