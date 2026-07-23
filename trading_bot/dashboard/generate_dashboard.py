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


CALIBRATION_BINS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def _won(s: dict) -> bool:
    # Settlements logged before "won" existed on the schema predate this dashboard
    # feature - derive it from realized_pnl rather than losing them from the stats.
    if "won" in s:
        return bool(s["won"])
    return s.get("realized_pnl", 0.0) > 0


def _win_rate_and_calibration(settlements: list[dict]) -> dict:
    total = len(settlements)
    wins = sum(1 for s in settlements if _won(s))

    by_strategy: dict[str, dict] = {}
    for s in settlements:
        strat = s.get("strategy", "?")
        b = by_strategy.setdefault(strat, {"total": 0, "wins": 0, "realized_pnl": 0.0})
        b["total"] += 1
        b["wins"] += 1 if _won(s) else 0
        b["realized_pnl"] += s.get("realized_pnl", 0.0)
    for b in by_strategy.values():
        b["win_rate"] = b["wins"] / b["total"] if b["total"] else None

    # Calibration: does a strategy's stated confidence actually predict outcomes?
    # A well-calibrated 70%-confidence trade should win ~70% of the time. Only
    # settlements that actually recorded a confidence can inform this - older
    # entries predating that field are silently excluded, not miscounted.
    calibration = []
    for lo, hi in CALIBRATION_BINS:
        bucket = [s for s in settlements if s.get("confidence") is not None and s["confidence"] > 0 and lo <= s["confidence"] < hi]
        if not bucket:
            continue
        calibration.append(
            {
                "range": f"{lo:.0%}-{min(hi, 1.0):.0%}",
                "count": len(bucket),
                "avg_stated_confidence": sum(s["confidence"] for s in bucket) / len(bucket),
                "actual_win_rate": sum(1 for s in bucket if _won(s)) / len(bucket),
            }
        )

    return {
        "total_settled": total,
        "wins": wins,
        "win_rate": wins / total if total else None,
        "by_strategy": by_strategy,
        "calibration": calibration,
    }


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
            "confidence": p.confidence,
        }
        for p in state.positions.values()
    ]

    account_points = read_jsonl(ACCOUNT_LOG, limit=1000)
    decisions = list(reversed(read_jsonl(DECISIONS_LOG, limit=200)))
    orders = list(reversed(read_jsonl(ORDERS_LOG, limit=200)))
    cycles = list(reversed(read_jsonl(CYCLES_LOG, limit=50)))

    filled_orders = [o for o in orders if o.get("status") == "filled"]
    rejected_orders = [o for o in orders if o.get("status") == "rejected"]

    all_decisions = read_jsonl(DECISIONS_LOG)
    settlements = [d for d in all_decisions if d.get("type") == "settlement"]
    performance = _win_rate_and_calibration(settlements)

    directional = broker.directional_exposure_by_underlying()
    directional_list = sorted(
        (
            {"underlying": u, **{k: v for k, v in g.items() if k != "strategies"}, "strategies": g["strategies"]}
            for u, g in directional.items()
        ),
        key=lambda g: g["total_exposure"],
        reverse=True,
    )

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
            "max_exposure_per_underlying_pct": cfg.risk.max_exposure_per_underlying_pct,
            "take_profit_pct": cfg.risk.take_profit_pct,
            "stop_loss_pct": cfg.risk.stop_loss_pct,
        },
        "positions": positions,
        "account_curve": account_points,
        "decisions": decisions,
        "orders": orders,
        "cycles": cycles,
        "directional_exposure": directional_list,
        "performance": performance,
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
