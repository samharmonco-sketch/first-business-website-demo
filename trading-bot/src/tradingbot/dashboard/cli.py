"""Lightweight CLI dashboard: current positions, open orders, recent
decisions, and account balance/PnL. No external dependencies beyond what
the rest of the bot already needs."""
from __future__ import annotations

from ..adapters.base import ExchangeAdapter
from ..logging_setup import BotLogs
from ..risk.manager import RiskManager
from ..state_store import StateStore


def _fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def print_status(adapter: ExchangeAdapter, store: StateStore, risk_manager: RiskManager, logs: BotLogs) -> None:
    account = adapter.get_account_state()

    print("=" * 70)
    print(" TRADING BOT STATUS")
    print("=" * 70)
    print(f" Mode: {'LIVE' if getattr(adapter, 'name', '') == 'kalshi' else 'PAPER'}")
    print(f" Kill switch: {'ENGAGED' if risk_manager.kill_switch.is_active() else 'off'}")
    print(f" Daily halt: {'YES -- loss limit hit' if store.is_daily_halted() else 'no'}")
    print(f" Day-one mode: {'active' if risk_manager.day_one_active else 'complete / disabled'}")
    print(f" Balance: {_fmt_money(account.balance_usd)}")
    print(f" Total exposure: {_fmt_money(account.total_exposure_usd)}")
    print(f" Realized PnL today: {_fmt_money(store.get_daily_pnl())}")
    print()

    print(f" POSITIONS ({len(account.positions)})")
    print("-" * 70)
    if not account.positions:
        print("  (none)")
    else:
        print(f"  {'TICKER':<20}{'SIDE':<6}{'QTY':<8}{'AVG PRICE':<12}{'STRATEGY':<15}")
        for p in account.positions:
            print(f"  {p.ticker:<20}{p.side.value:<6}{p.quantity:<8}{p.avg_price:<12.3f}{p.strategy_name:<15}")
    print()

    open_orders = store.get_open_orders()
    print(f" OPEN ORDERS ({len(open_orders)})")
    print("-" * 70)
    if not open_orders:
        print("  (none)")
    else:
        for o in open_orders:
            print(f"  {o['ticker']:<20}{o['side']:<6}{o['action']:<6}{o['count']:<6}@{o['limit_price']:.3f}  status={o['status']}")
    print()

    print(" RECENT DECISIONS (last 15)")
    print("-" * 70)
    for d in logs.decisions.tail(15):
        traded_marker = "TRADE" if d.get("traded") else "no-trade"
        print(f"  [{traded_marker:>8}] {d.get('strategy'):<15} {d.get('ticker'):<18} "
              f"{d.get('action'):<10} edge={d.get('edge', 0):.3f} conf={d.get('confidence', 0):.2f}")
        reason = d.get("reject_reason") or d.get("reasoning", "")
        print(f"             {reason[:120]}")
    print("=" * 70)
