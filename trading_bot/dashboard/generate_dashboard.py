#!/usr/bin/env python3
"""Renders the current bot state + logs into a single self-contained HTML
dashboard, per requirement #8 ("a simple CLI or lightweight local dashboard to
see current positions, open orders, and recent decisions"). Reads only the
JSONL logs and state.json - never touches the network - so it's safe to run
on every cycle (see run_cycle.py) or on demand.

Usage: python -m trading_bot.dashboard.generate_dashboard [output_path]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from trading_bot.config import load_config
from trading_bot.config import KILL_SWITCH_PATH
from trading_bot.exchanges.paper_broker import PaperBroker
from trading_bot.logging_setup import ACCOUNT_LOG, CYCLES_LOG, DECISIONS_LOG, ORDERS_LOG, read_jsonl

TEMPLATE_PATH = Path(__file__).parent / "template.html"


def build_payload() -> dict:
    cfg = load_config()
    broker = PaperBroker(cfg.paper_starting_bankroll)
    state = broker.state

    positions = [
        {
            "ticker": p.ticker,
            "side": p.side.value,
            "contracts": p.contracts,
            "avg_price_cents": p.avg_price_cents,
            "cost_basis": p.cost_basis(),
            "strategy": p.strategy,
            "opened_at": p.opened_at,
        }
        for p in state.positions.values()
    ]

    account_points = read_jsonl(ACCOUNT_LOG, limit=1000)
    decisions = list(reversed(read_jsonl(DECISIONS_LOG, limit=200)))
    orders = list(reversed(read_jsonl(ORDERS_LOG, limit=200)))
    cycles = list(reversed(read_jsonl(CYCLES_LOG, limit=50)))

    filled_orders = [o for o in orders if o.get("status") == "filled"]
    rejected_orders = [o for o in orders if o.get("status") == "rejected"]

    return {
        "generated_at": account_points[-1]["timestamp"] if account_points else None,
        "mode": cfg.mode,
        "kill_switch_engaged": KILL_SWITCH_PATH.exists(),
        "bankroll": {
            "starting": state.starting_bankroll,
            "equity": state.equity,
            "cash": state.cash,
            "realized_pnl": state.realized_pnl,
            "daily_pnl_pct": state.daily_pnl_pct,
            "exposure": state.unrealized_exposure,
        },
        "risk": {
            "max_position_abs": cfg.risk.max_position_abs,
            "max_total_exposure_pct": cfg.risk.max_total_exposure_pct,
            "daily_loss_limit_pct": cfg.risk.daily_loss_limit_pct,
        },
        "positions": positions,
        "account_curve": account_points,
        "decisions": decisions,
        "orders": orders,
        "cycles": cycles,
        "stats": {
            "cycles_run": len(read_jsonl(CYCLES_LOG)),
            "signals_evaluated": sum(c.get("signals_evaluated", 0) for c in cycles),
            "trades_filled": len(filled_orders),
            "trades_rejected": len(rejected_orders),
        },
    }


def render(output_path: Path) -> Path:
    payload = build_payload()
    template = TEMPLATE_PATH.read_text()
    html = template.replace("__DASHBOARD_DATA__", json.dumps(payload, default=str))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("trading_bot/dashboard/output.html")
    render(out)
    print(f"Dashboard written to {out}")
