import tempfile
from pathlib import Path

import pytest

from tradingbot.config import BankrollConfig, DayOneConfig, RiskConfig
from tradingbot.models import AccountState, Signal, SignalAction
from tradingbot.risk.manager import RiskManager
from tradingbot.state_store import StateStore


@pytest.fixture
def risk_setup(tmp_path):
    bankroll = BankrollConfig(
        starting_bankroll_usd=1500.0, max_position_usd=50.0, max_position_pct_of_bankroll=0.05,
        max_total_exposure_usd=300.0, daily_loss_limit_usd=100.0, per_market_exposure_usd=75.0,
        per_strategy_exposure_usd=150.0, min_edge_to_trade=0.05, min_confidence_to_trade=0.55,
    )
    day_one = DayOneConfig(
        enabled=True, min_edge_to_trade=0.02, min_confidence_to_trade=0.50,
        max_position_usd=5.0, max_total_exposure_usd=15.0, disable_after_first_trade=True,
    )
    risk_cfg = RiskConfig(kill_switch_file="KILL_SWITCH", state_db_path="state.db")
    store = StateStore(tmp_path / "state.db", bankroll.starting_bankroll_usd)
    rm = RiskManager(bankroll, day_one, risk_cfg, store, tmp_path)
    return rm, store, bankroll, day_one


def make_signal(edge=0.1, confidence=0.7, size_usd=40.0, ticker="KXBTC-TEST", strategy="crypto_momentum",
                 action=SignalAction.BUY_YES):
    return Signal(strategy_name=strategy, ticker=ticker, action=action, size_usd=size_usd,
                  confidence=confidence, edge=edge, reasoning="test signal")


def make_account(balance=1500.0, positions=None, total_exposure=0.0):
    return AccountState(balance_usd=balance, positions=positions or [], total_exposure_usd=total_exposure)


def test_day_one_permissive_thresholds_allow_smaller_edge(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    assert rm.day_one_active is True
    signal = make_signal(edge=0.03, confidence=0.55, size_usd=10.0)  # fails steady-state, passes day-one
    decision = rm.evaluate(signal, make_account())
    assert decision.approved
    assert decision.approved_size_usd <= day_one.max_position_usd


def test_day_one_disables_after_first_trade(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    assert rm.day_one_active is True
    rm.mark_day_one_trade_executed()
    assert rm.day_one_active is False
    min_edge, min_conf = rm.thresholds()
    assert min_edge == bankroll.min_edge_to_trade


def test_steady_state_rejects_below_min_edge(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    signal = make_signal(edge=0.01, confidence=0.9)
    decision = rm.evaluate(signal, make_account())
    assert not decision.approved
    assert "edge" in decision.reason


def test_steady_state_rejects_below_min_confidence(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    signal = make_signal(edge=0.2, confidence=0.3)
    decision = rm.evaluate(signal, make_account())
    assert not decision.approved
    assert "confidence" in decision.reason


def test_max_position_size_capped(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    signal = make_signal(edge=0.2, confidence=0.9, size_usd=1000.0)
    decision = rm.evaluate(signal, make_account())
    assert decision.approved
    assert decision.approved_size_usd <= bankroll.max_position_usd


def test_max_total_exposure_enforced(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    account = make_account(total_exposure=290.0)
    signal = make_signal(edge=0.2, confidence=0.9, size_usd=50.0)
    decision = rm.evaluate(signal, account)
    assert decision.approved
    assert decision.approved_size_usd <= 10.0  # only $10 of headroom left


def test_total_exposure_at_cap_rejects(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    account = make_account(total_exposure=300.0)
    signal = make_signal(edge=0.2, confidence=0.9, size_usd=50.0)
    decision = rm.evaluate(signal, account)
    assert not decision.approved
    assert "exposure" in decision.reason


def test_daily_loss_limit_halts_trading(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    store.add_realized_pnl(-100.0)
    signal = make_signal(edge=0.2, confidence=0.9)
    decision = rm.evaluate(signal, make_account())
    assert not decision.approved
    assert "loss limit" in decision.reason
    # Once halted, stays halted for the rest of the day regardless of edge/confidence.
    assert store.is_daily_halted()


def test_kill_switch_blocks_all_trades(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.kill_switch.engage()
    signal = make_signal(edge=0.5, confidence=0.99)
    decision = rm.evaluate(signal, make_account())
    assert not decision.approved
    assert "kill switch" in decision.reason
    rm.kill_switch.disengage()
    decision2 = rm.evaluate(signal, make_account())
    assert decision2.approved


def test_hold_signal_never_approved(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    signal = make_signal(action=SignalAction.HOLD, size_usd=0.0, edge=0.0, confidence=0.0)
    decision = rm.evaluate(signal, make_account())
    assert not decision.approved


def test_per_market_exposure_cap(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    from tradingbot.models import Position, Side
    positions = [Position(ticker="KXBTC-TEST", side=Side.YES, quantity=70, avg_price=1.0, strategy_name="crypto_momentum")]
    account = make_account(positions=positions, total_exposure=70.0)
    signal = make_signal(edge=0.2, confidence=0.9, size_usd=50.0, ticker="KXBTC-TEST")
    decision = rm.evaluate(signal, account)
    assert decision.approved
    assert decision.approved_size_usd <= 5.0  # per_market_exposure_usd=75, 70 used


def test_per_strategy_exposure_cap(risk_setup):
    rm, store, bankroll, day_one = risk_setup
    rm.mark_day_one_trade_executed()
    from tradingbot.models import Position, Side
    positions = [Position(ticker="KXBTC-A", side=Side.YES, quantity=140, avg_price=1.0, strategy_name="crypto_momentum")]
    account = make_account(positions=positions, total_exposure=140.0)
    signal = make_signal(edge=0.2, confidence=0.9, size_usd=50.0, ticker="KXBTC-B", strategy="crypto_momentum")
    decision = rm.evaluate(signal, account)
    assert decision.approved
    assert decision.approved_size_usd <= 10.0  # per_strategy_exposure_usd=150, 140 used
