from tradingbot.models import AccountState, Market, SignalAction
from tradingbot.strategies.crypto_momentum import CryptoMomentumStrategy


def make_market(ticker="KXBTC-TEST", yes_bid=0.50, yes_ask=0.52):
    return Market(
        ticker=ticker, series_ticker="KXBTC", title="BTC up test market",
        yes_bid=yes_bid, yes_ask=yes_ask, no_bid=1 - yes_ask, no_ask=1 - yes_bid,
        volume=100, close_ts=9999999999,
    )


def test_momentum_insufficient_history_holds():
    strat = CryptoMomentumStrategy({"lookback_ticks": 20, "momentum_threshold": 0.015, "mean_reversion_zscore": 2.0})
    account = AccountState(balance_usd=1000.0)
    signal = strat.evaluate(make_market(), account)
    assert signal.action == SignalAction.HOLD
    assert "insufficient" in signal.reasoning


def test_momentum_detects_upward_trend():
    strat = CryptoMomentumStrategy({"lookback_ticks": 10, "momentum_threshold": 0.015, "mean_reversion_zscore": 2.0})
    account = AccountState(balance_usd=1000.0)
    # Feed a rising price series -- yes_mid goes from 0.50 up to 0.60.
    prices = [0.50, 0.51, 0.52, 0.53, 0.54, 0.55, 0.56, 0.57, 0.58, 0.60]
    signal = None
    for p in prices:
        signal = strat.evaluate(make_market(yes_bid=p - 0.01, yes_ask=p + 0.01), account)
    assert signal.action == SignalAction.BUY_YES
    assert signal.edge > 0
    assert signal.confidence > 0.5


def test_momentum_detects_downward_trend():
    strat = CryptoMomentumStrategy({"lookback_ticks": 10, "momentum_threshold": 0.015, "mean_reversion_zscore": 2.0})
    account = AccountState(balance_usd=1000.0)
    prices = [0.60, 0.59, 0.58, 0.57, 0.56, 0.55, 0.54, 0.53, 0.52, 0.50]
    signal = None
    for p in prices:
        signal = strat.evaluate(make_market(yes_bid=p - 0.01, yes_ask=p + 0.01), account)
    assert signal.action == SignalAction.BUY_NO


def test_momentum_no_signal_when_flat():
    strat = CryptoMomentumStrategy({"lookback_ticks": 10, "momentum_threshold": 0.05, "mean_reversion_zscore": 3.0})
    account = AccountState(balance_usd=1000.0)
    prices = [0.50] * 10
    signal = None
    for p in prices:
        signal = strat.evaluate(make_market(yes_bid=p - 0.01, yes_ask=p + 0.01), account)
    assert signal.action == SignalAction.HOLD


def test_dummy_buy_always_signals():
    from tradingbot.strategies.dummy_buy import DummyAlwaysSmallBuyStrategy
    strat = DummyAlwaysSmallBuyStrategy({"size_usd": 2.0})
    account = AccountState(balance_usd=1000.0)
    signal = strat.evaluate(make_market(), account)
    assert signal.action == SignalAction.BUY_YES
    assert signal.size_usd == 2.0
    assert signal.is_actionable
