#!/usr/bin/env python3
"""Lightweight status/control CLI, per requirement #8 and the kill-switch
requirement in #5. Usage (from repo root):
  python -m trading_bot.cli status
  python -m trading_bot.cli kill [--flatten]
  python -m trading_bot.cli resume
  python -m trading_bot.cli reset-daily-halt
"""
from __future__ import annotations

import sys

from trading_bot.exchanges.paper_broker import PaperBroker
from trading_bot.config import load_config
from trading_bot.logging_setup import CYCLES_LOG, DECISIONS_LOG, log_decision, read_jsonl
from trading_bot.models import now_iso
from trading_bot.risk.risk_manager import RiskManager


def cmd_status() -> None:
    cfg = load_config()
    broker = PaperBroker(cfg.paper_starting_bankroll)
    state = broker.state
    from trading_bot.config import KILL_SWITCH_PATH

    print(f"Mode: {cfg.mode}")
    print(f"Kill switch engaged: {KILL_SWITCH_PATH.exists()}")
    print(f"Equity: ${state.equity:,.2f}  Cash: ${state.cash:,.2f}  Realized PnL: ${state.realized_pnl:,.2f}")
    print(f"Daily PnL: {state.daily_pnl_pct:+.2%} (limit: -{cfg.risk.daily_loss_limit_pct:.0%})")
    print(f"Open positions: {len(state.positions)}")
    for key, pos in state.positions.items():
        print(f"  {key}: {pos.contracts} @ {pos.avg_price_cents:.0f}c (strategy={pos.strategy})")

    print("\nLast 5 cycles:")
    for c in read_jsonl(CYCLES_LOG, limit=5):
        print(f"  {c.get('timestamp')} crypto={c.get('crypto_markets_seen')} sports={c.get('sports_markets_seen')} "
              f"trades={c.get('trades_executed')} no_trades={c.get('no_trades')} errors={c.get('errors')}")

    print("\nLast 10 decisions:")
    for d in read_jsonl(DECISIONS_LOG, limit=10):
        kind = "TRADE" if d.get("action") else ("VETO" if d.get("type") == "risk_veto" else "no-trade")
        print(f"  [{kind}] {d.get('strategy')} {d.get('ticker')}: {str(d.get('reasoning'))[:160]}")


def cmd_kill(flatten: bool) -> None:
    RiskManager.engage_kill_switch(flatten=flatten)
    print("Kill switch engaged. No new trades will be placed." + (" Flatten requested." if flatten else ""))


def cmd_resume() -> None:
    RiskManager.disengage_kill_switch()
    print("Kill switch disengaged. Trading may resume.")


def cmd_reset_daily_halt() -> None:
    cfg = load_config()
    broker = PaperBroker(cfg.paper_starting_bankroll)
    result = broker.reset_daily_loss_tracking()
    log_decision({"type": "manual_daily_halt_reset", "evaluated_at": now_iso(), **result})
    print("Daily-loss-limit window manually reset (logged for audit):")
    print(f"  before: {result['before']}")
    print(f"  after:  {result['after']}")
    print("Note: the daily loss limit itself is NOT disabled - a fresh -{:.0%} drop from this new baseline will halt again.".format(cfg.risk.daily_loss_limit_pct))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "kill":
        cmd_kill(flatten="--flatten" in sys.argv[2:])
    elif cmd == "resume":
        cmd_resume()
    elif cmd == "reset-daily-halt":
        cmd_reset_daily_halt()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
