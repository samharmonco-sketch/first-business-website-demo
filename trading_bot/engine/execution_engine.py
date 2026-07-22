"""The autonomous execution loop, per requirement #1: poll markets, evaluate
every active strategy against live data, place orders automatically when a
strategy signals a trade, and log every decision - trade or no-trade - with
the reasoning behind it. run_cycle() is one full pass and is meant to be
called repeatedly (by a while-loop for continuous local running, or by a
scheduled Routine here).
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from anthropic import Anthropic

from ..config import Config, load_config
from ..exchanges.kalshi import KalshiAdapter, KalshiAuthError
from ..exchanges.paper_broker import PaperBroker
from ..exchanges.price_history import PriceHistory
from ..logging_setup import ORDERS_LOG, log_account, log_cycle, log_decision, log_order, read_jsonl
from ..models import MarketCategory, TradeSignal, now_iso
from ..risk.risk_manager import RiskManager
from ..sports.odds_client import OddsApiClient
from ..strategies.day_one import DayOneStrategy
from ..strategies.mispricing import CryptoMispricingStrategy
from ..strategies.momentum import CryptoMomentumStrategy
from ..strategies.sports_ai import SportsAiStrategy

logger = logging.getLogger("trading_bot.engine")


class EngineContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.kalshi = KalshiAdapter(cfg.kalshi)
        self.broker = PaperBroker(cfg.paper_starting_bankroll)
        self.risk = RiskManager(cfg, self.broker)
        self.history = PriceHistory()
        self.odds_client = OddsApiClient(cfg.odds_api_key, cfg.odds_api_base_url)
        self.anthropic_client = Anthropic(api_key=cfg.anthropic_api_key) if cfg.anthropic_api_key else None

        self.crypto_strategies = [CryptoMispricingStrategy(), CryptoMomentumStrategy(self.history)]
        self.sports_strategies = []
        if self.anthropic_client and cfg.odds_api_key:
            self.sports_strategies.append(SportsAiStrategy(self.odds_client, self.anthropic_client, cfg.anthropic_model))

        self.day_one_crypto = DayOneStrategy(self.crypto_strategies)
        self.day_one_sports = DayOneStrategy(self.sports_strategies) if self.sports_strategies else None


def is_first_ever_run() -> bool:
    orders = read_jsonl(ORDERS_LOG)
    return not any(o.get("status") == "filled" for o in orders)


def _log_signal_result(result) -> None:
    record = asdict(result)
    record["evaluated_at"] = now_iso()
    log_decision(record)


def _execute_signal(ctx: EngineContext, signal: TradeSignal, day_one_mode: bool, market) -> None:
    verdict = ctx.risk.check(signal, day_one_mode=day_one_mode)
    if not verdict.approved:
        log_decision(
            {
                "type": "risk_veto",
                "strategy": signal.strategy,
                "ticker": signal.ticker,
                "reasoning": signal.reasoning,
                "risk_reason": verdict.reason,
                "evaluated_at": now_iso(),
            }
        )
        logger.info("RISK VETO [%s] %s: %s", signal.strategy, signal.ticker, verdict.reason)
        return

    contracts = verdict.adjusted_contracts or signal.size_contracts
    order = ctx.broker.execute(market, signal.action, signal.side, contracts, signal.strategy)
    order.strategy = signal.strategy
    log_order(asdict(order))
    logger.info(
        "ORDER %s [%s] %s %s x%d @ %.0fc - %s",
        order.status.upper(),
        signal.strategy,
        signal.ticker,
        signal.side.value,
        contracts,
        signal.limit_price_cents,
        signal.reasoning[:200],
    )


def run_cycle(ctx: EngineContext | None = None) -> dict:
    cfg = ctx.cfg if ctx else load_config()
    if ctx is None:
        ctx = EngineContext(cfg)

    day_one_mode = cfg.risk.day_one_mode_enabled and is_first_ever_run()
    signals_evaluated = 0
    trades_executed = 0
    no_trades = 0
    errors = []

    try:
        crypto_markets = ctx.kalshi.get_markets(MarketCategory.CRYPTO)
    except KalshiAuthError as exc:
        errors.append(str(exc))
        crypto_markets = []
    except Exception as exc:  # noqa: BLE001
        errors.append(f"crypto market fetch failed: {exc}")
        crypto_markets = []

    try:
        sports_markets = ctx.kalshi.get_markets(MarketCategory.SPORTS)
    except KalshiAuthError as exc:
        if str(exc) not in errors:
            errors.append(str(exc))
        sports_markets = []
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sports market fetch failed: {exc}")
        sports_markets = []

    for market in crypto_markets:
        ctx.history.record(market.ticker, market.implied_yes_prob)
        strategies = [ctx.day_one_crypto] if day_one_mode else ctx.crypto_strategies
        for strategy in strategies:
            result = strategy.evaluate(market, ctx.broker.state, cfg.risk, day_one_mode=day_one_mode)
            signals_evaluated += 1
            _log_signal_result(result)
            if isinstance(result, TradeSignal):
                _execute_signal(ctx, result, day_one_mode, market)
                trades_executed += 1
            else:
                no_trades += 1
    ctx.history.save()

    sports_strategy_set = [ctx.day_one_sports] if day_one_mode else ctx.sports_strategies
    sports_strategy_set = [s for s in sports_strategy_set if s is not None]
    for market in sports_markets:
        for strategy in sports_strategy_set:
            result = strategy.evaluate(market, ctx.broker.state, cfg.risk, day_one_mode=day_one_mode)
            signals_evaluated += 1
            _log_signal_result(result)
            if isinstance(result, TradeSignal):
                _execute_signal(ctx, result, day_one_mode, market)
                trades_executed += 1
            else:
                no_trades += 1

    log_account(
        {
            "equity": ctx.broker.state.equity,
            "cash": ctx.broker.state.cash,
            "realized_pnl": ctx.broker.state.realized_pnl,
            "open_positions": len(ctx.broker.state.positions),
            "daily_pnl_pct": ctx.broker.state.daily_pnl_pct,
            "timestamp": now_iso(),
        }
    )

    summary = {
        "timestamp": now_iso(),
        "mode": cfg.mode,
        "day_one_mode": day_one_mode,
        "crypto_markets_seen": len(crypto_markets),
        "sports_markets_seen": len(sports_markets),
        "signals_evaluated": signals_evaluated,
        "trades_executed": trades_executed,
        "no_trades": no_trades,
        "errors": errors,
        "equity": ctx.broker.state.equity,
    }
    log_cycle(summary)
    logger.info(
        "CYCLE done: crypto=%d sports=%d signals=%d trades=%d no_trades=%d errors=%s equity=$%.2f",
        len(crypto_markets),
        len(sports_markets),
        signals_evaluated,
        trades_executed,
        no_trades,
        errors,
        ctx.broker.state.equity,
    )
    return summary
