"""CLI entrypoint.

Usage:
    python -m tradingbot.main run              # start the autonomous loop
    python -m tradingbot.main once              # run a single poll cycle (for testing)
    python -m tradingbot.main status            # print the dashboard snapshot
    python -m tradingbot.main serve             # local web dashboard (http://127.0.0.1:8765)
    python -m tradingbot.main kill              # engage the kill switch (no new trades)
    python -m tradingbot.main kill --flatten    # kill switch + close all open positions
    python -m tradingbot.main resume            # disengage the kill switch
"""
from __future__ import annotations

import argparse
import sys

from .adapters.kalshi import KalshiAdapter
from .adapters.paper import PaperTradingAdapter
from .config import load_config
from .dashboard.cli import print_status
from .engine.execution import ExecutionEngine
from .logging_setup import BotLogs
from .risk.manager import RiskManager
from .state_store import StateStore
from .strategies.registry import build_strategies


def build_runtime(config_path: str | None = None):
    config = load_config(config_path)

    log_dir = config.resolve_path(config.logging.log_dir)
    logs = BotLogs(log_dir, config.logging.decisions_file, config.logging.orders_file,
                    config.logging.account_file, level=config.logging.level)

    db_path = config.resolve_path(config.risk.state_db_path)
    store = StateStore(db_path, config.bankroll.starting_bankroll_usd)

    market_data_adapter = KalshiAdapter(config.exchange, logs=logs)

    if config.is_live:
        logs.logger.warning("MODE=LIVE -- real orders will be placed against the exchange.")
        adapter = market_data_adapter
    else:
        adapter = PaperTradingAdapter(market_data_adapter, store, logs=logs)
        logs.logger.info("MODE=PAPER -- simulated fills against live market data. No real orders will be placed.")

    risk_manager = RiskManager(config.bankroll, config.day_one, config.risk, store, config.root_dir)
    # sports_ai always looks up Kalshi market prices via the read-only public
    # market-data adapter, even in live mode -- it never needs auth for this.
    strategies = build_strategies(
        config.strategies, anthropic_config=config.anthropic, log_dir=log_dir,
        odds_api_key=config.odds_api.api_key, market_data_adapter=market_data_adapter,
    )

    engine = ExecutionEngine(config, adapter, strategies, risk_manager, store, logs)
    return config, adapter, store, risk_manager, logs, engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous prediction-market trading bot")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the autonomous execution loop")
    sub.add_parser("once", help="run a single poll cycle and exit")
    sub.add_parser("status", help="print current positions/orders/decisions/PnL")
    serve_parser = sub.add_parser("serve", help="local web dashboard (auto-refreshing, read-only)")
    serve_parser.add_argument("--port", type=int, default=8765)
    kill_parser = sub.add_parser("kill", help="engage the kill switch")
    kill_parser.add_argument("--flatten", action="store_true", help="also close all open positions")
    sub.add_parser("resume", help="disengage the kill switch")

    args = parser.parse_args()
    config, adapter, store, risk_manager, logs, engine = build_runtime(args.config)

    if args.command == "run":
        engine.run_forever()
    elif args.command == "once":
        summary = engine.run_cycle()
        print(f"Cycle complete: {summary}")
    elif args.command == "status":
        print_status(adapter, store, risk_manager, logs)
    elif args.command == "serve":
        from .dashboard.web import run_server
        run_server(adapter, store, risk_manager, logs, port=args.port,
                   poll_interval_seconds=config.polling.interval_seconds,
                   use_demo=config.exchange.use_demo)
    elif args.command == "kill":
        risk_manager.kill_switch.engage()
        print("Kill switch engaged. No new trades will be placed.")
        if args.flatten:
            account_state = adapter.get_account_state()
            if account_state.positions:
                results = adapter.flatten_all_positions(account_state)
                print(f"Flattened {len(results)} position(s).")
            else:
                print("No open positions to flatten.")
    elif args.command == "resume":
        risk_manager.kill_switch.disengage()
        print("Kill switch disengaged. Trading may resume on the next cycle.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
