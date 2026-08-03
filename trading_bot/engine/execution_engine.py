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
from ..models import MarketCategory, OrderAction, TradeSignal, now_iso
from ..risk.risk_manager import RiskManager
from ..sports.odds_client import OddsApiClient
from ..strategies.day_one import DayOneStrategy
from ..strategies.mispricing import CryptoMispricingStrategy, compute_atm_vols
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

        # crypto_momentum disabled: 19.8% win rate, -$730.85 net realized (21W/85L) -
        # it was the strategy driving the account from $1,000 to under $85. Only
        # crypto_mispricing has demonstrated real edge (74%+ win rate, net positive).
        self.crypto_strategies = [CryptoMispricingStrategy()]
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


def _execute_signal(ctx: EngineContext, signal: TradeSignal, day_one_mode: bool, market) -> bool:
    """Returns whether an order actually filled - the caller uses this to
    count real trades, not just strategy signals that got vetoed or
    rejected (see the trades_executed note in run_cycle())."""
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
        return False

    contracts = verdict.adjusted_contracts or signal.size_contracts
    vol_used = signal.extra.get("vol_used", 0.0)
    order = ctx.broker.execute(market, signal.action, signal.side, contracts, signal.strategy, signal.confidence, vol_used)
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
    return order.status == "filled"


def _check_exits(ctx: EngineContext, markets: list) -> list[dict]:
    """Take-profit / stop-loss: the only exit path before this was holding
    every position to expiry. Marks each open position to the current bid
    (what you'd actually get selling right now, not the more flattering mid)
    and closes it early if it's moved far enough in either direction. Reuses
    this cycle's already-fetched market snapshots - no extra API calls.
    """
    by_ticker = {m.ticker: m for m in markets}
    exits = []
    for key, pos in list(ctx.broker.state.positions.items()):
        market = by_ticker.get(pos.ticker)
        if not market:
            continue
        current_bid = market.yes_bid if pos.side.value == "yes" else market.no_bid
        if pos.avg_price_cents <= 0:
            continue
        unrealized_pct = (current_bid - pos.avg_price_cents) / pos.avg_price_cents

        if unrealized_pct >= ctx.cfg.risk.take_profit_pct:
            exit_reason = "take_profit"
        elif unrealized_pct <= -ctx.cfg.risk.stop_loss_pct:
            exit_reason = "stop_loss"
        else:
            continue

        contracts_sold = pos.contracts  # broker.execute() mutates pos.contracts to 0 (full sell) below
        order = ctx.broker.execute(market, OrderAction.SELL, pos.side, contracts_sold, pos.strategy)
        order.strategy = pos.strategy
        log_order(asdict(order))
        log_decision(
            {
                "type": "exit",
                "exit_reason": exit_reason,
                "strategy": pos.strategy,
                "ticker": pos.ticker,
                "side": pos.side.value,
                "contracts": contracts_sold,
                "entry_price_cents": pos.avg_price_cents,
                "exit_price_cents": current_bid,
                "unrealized_pct": unrealized_pct,
                "evaluated_at": now_iso(),
            }
        )
        logger.info(
            "EXIT %s [%s] %s %s x%d @ %.0fc (entry %.0fc, %+.1f%%)",
            exit_reason.upper(), pos.strategy, pos.ticker, pos.side.value, contracts_sold, current_bid,
            pos.avg_price_cents, unrealized_pct * 100,
        )
        exits.append({"ticker": pos.ticker, "reason": exit_reason})
    return exits


def run_cycle(ctx: EngineContext | None = None) -> dict:
    cfg = ctx.cfg if ctx else load_config()
    if ctx is None:
        ctx = EngineContext(cfg)

    day_one_mode = cfg.risk.day_one_mode_enabled and is_first_ever_run()
    signals_evaluated = 0
    trades_executed = 0
    trades_vetoed = 0
    no_trades = 0
    errors = []

    settlements = ctx.broker.settle_resolved_positions(ctx.kalshi.get_market_result)
    for s in settlements:
        log_decision({"type": "settlement", "evaluated_at": now_iso(), **s})
        # Calibration detail logged per-trade as it settles, not just batched into
        # the dashboard's summary table - confidence and vol are exactly the two
        # numbers a calibration check needs, so they're in the log line itself.
        logger.info(
            "SETTLED [%s] %s %s x%d -> %s, won=%s, realized_pnl=$%.2f, confidence=%.3f, vol_used=%.1f%%",
            s["strategy"], s["ticker"], s["side"], s["contracts"], s["result"], s["won"],
            s["realized_pnl"], s.get("confidence", 0.0), s.get("vol_used", 0.0) * 100,
        )

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

    exits = _check_exits(ctx, crypto_markets + sports_markets)

    atm_vols = compute_atm_vols(crypto_markets)
    for market in crypto_markets:
        event = market.raw.get("event_ticker")
        if event in atm_vols:
            market.raw["_implied_vol"] = atm_vols[event]
        ctx.history.record(market.ticker, market.implied_yes_prob)
        strategies = [ctx.day_one_crypto] if day_one_mode else ctx.crypto_strategies
        for strategy in strategies:
            result = strategy.evaluate(market, ctx.broker.state, cfg.risk, day_one_mode=day_one_mode)
            signals_evaluated += 1
            _log_signal_result(result)
            if isinstance(result, TradeSignal):
                if _execute_signal(ctx, result, day_one_mode, market):
                    trades_executed += 1
                else:
                    trades_vetoed += 1
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
                if _execute_signal(ctx, result, day_one_mode, market):
                    trades_executed += 1
                else:
                    trades_vetoed += 1
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
        "trades_vetoed": trades_vetoed,
        "no_trades": no_trades,
        "settlements": len(settlements),
        "exits": len(exits),
        "errors": errors,
        "equity": ctx.broker.state.equity,
    }
    log_cycle(summary)
    logger.info(
        "CYCLE done: crypto=%d sports=%d signals=%d trades=%d vetoed=%d no_trades=%d exits=%d errors=%s equity=$%.2f",
        len(crypto_markets),
        len(sports_markets),
        signals_evaluated,
        trades_executed,
        trades_vetoed,
        no_trades,
        len(exits),
        errors,
        ctx.broker.state.equity,
    )
    return summary
