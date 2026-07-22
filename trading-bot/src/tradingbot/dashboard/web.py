"""Lightweight local web dashboard.

One browser tab, auto-refreshing every 5 seconds, showing balance, open
positions, open orders, and recent decisions -- so you don't need several
terminal windows open to see what the bot is doing. Stdlib only, no new
dependencies. Read-only: this never places orders, it only displays state
that the running bot (in its own terminal) has already written to disk.
"""
from __future__ import annotations

import html
import http.server
import socketserver
import time

from ..adapters.base import ExchangeAdapter
from ..logging_setup import BotLogs
from ..risk.manager import RiskManager
from ..state_store import StateStore


def _fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:12px;font-weight:600;">{html.escape(text)}</span>'
    )


def _heartbeat(logs: BotLogs, poll_interval_seconds: int) -> tuple[str, str]:
    """Every poll cycle writes at least one decision, whether it trades,
    holds, or is halted -- so the newest decision's timestamp is a
    reliable "is the bot loop actually alive" signal, independent of
    whether anything traded recently."""
    latest = logs.decisions.tail(1)
    if not latest:
        return "no cycles logged yet", "#cf222e"

    age_seconds = time.time() - latest[0].get("ts", 0)
    if age_seconds < 0:
        age_seconds = 0
    if age_seconds < 60:
        age_text = f"{age_seconds:.0f}s ago"
    elif age_seconds < 3600:
        age_text = f"{age_seconds / 60:.0f}m ago"
    else:
        age_text = f"{age_seconds / 3600:.1f}h ago"

    if age_seconds <= poll_interval_seconds * 2:
        color = "#1a7f37"  # green -- on schedule
    elif age_seconds <= poll_interval_seconds * 5:
        color = "#9a6700"  # amber -- running late, not necessarily dead
    else:
        color = "#cf222e"  # red -- likely stopped (asleep, crashed, laptop closed)
    return f"last cycle: {age_text}", color


def _render_html(adapter: ExchangeAdapter, store: StateStore, risk_manager: RiskManager, logs: BotLogs,
                  poll_interval_seconds: int = 60) -> str:
    account = adapter.get_account_state()
    positions = account.positions
    open_orders = store.get_open_orders()
    decisions = list(reversed(logs.decisions.tail(50)))
    heartbeat_text, heartbeat_color = _heartbeat(logs, poll_interval_seconds)

    kill_active = risk_manager.kill_switch.is_active()
    halted = store.is_daily_halted()
    day_one = risk_manager.day_one_active

    positions_rows = "".join(
        f"<tr><td>{html.escape(p.ticker)}</td><td>{p.side.value}</td><td>{p.quantity}</td>"
        f"<td>{p.avg_price:.3f}</td><td>{html.escape(p.strategy_name)}</td>"
        f"<td>{_fmt_money(p.cost_basis_usd)}</td></tr>"
        for p in positions
    ) or "<tr><td colspan='6' class='empty'>No open positions</td></tr>"

    orders_rows = "".join(
        f"<tr><td>{html.escape(o['ticker'])}</td><td>{o['side']}</td><td>{o['action']}</td>"
        f"<td>{o['count']}</td><td>{o['limit_price']:.3f}</td><td>{o['status']}</td></tr>"
        for o in open_orders
    ) or "<tr><td colspan='6' class='empty'>No open orders</td></tr>"

    decision_rows = []
    for d in decisions:
        marker = _badge("TRADE", "#1a7f37") if d.get("traded") else _badge("no-trade", "#484f58")
        reason = html.escape((d.get("reject_reason") or d.get("reasoning", ""))[:200])
        decision_rows.append(
            f"<tr><td>{marker}</td><td>{html.escape(d.get('strategy',''))}</td>"
            f"<td>{html.escape(d.get('ticker',''))}</td><td>{html.escape(d.get('action',''))}</td>"
            f"<td>{d.get('edge',0):.3f}</td><td>{d.get('confidence',0):.2f}</td>"
            f"<td class='reason'>{reason}</td></tr>"
        )
    decisions_html = "".join(decision_rows) or "<tr><td colspan='7' class='empty'>No decisions logged yet</td></tr>"

    status_badges = "".join([
        _badge("KILL SWITCH ENGAGED", "#cf222e") if kill_active else _badge("kill switch: off", "#30363d"),
        _badge("DAILY LOSS HALT", "#cf222e") if halted else _badge("daily halt: no", "#30363d"),
        _badge("DAY-ONE MODE ACTIVE", "#9a6700") if day_one else _badge("day-one: complete", "#30363d"),
    ])

    mode_label = "LIVE" if adapter.name == "kalshi" else "PAPER"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Trading Bot Dashboard</title>
<style>
  body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,Helvetica,Arial,sans-serif; padding:24px; max-width:1100px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#8b949e; font-size:13px; margin-bottom:16px; }}
  .badges {{ margin-bottom:24px; }}
  .badges span {{ margin-right:8px; }}
  .stats {{ display:flex; gap:16px; margin-bottom:28px; flex-wrap:wrap; }}
  .stat {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 20px; min-width:150px; }}
  .stat .label {{ color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:0.5px; }}
  .stat .value {{ font-size:22px; font-weight:600; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:8px; font-size:13px; }}
  th {{ text-align:left; color:#8b949e; font-weight:500; padding:6px 10px; border-bottom:1px solid #30363d; }}
  td {{ padding:6px 10px; border-bottom:1px solid #21262d; vertical-align:top; }}
  td.reason {{ color:#8b949e; font-size:12px; max-width:420px; }}
  td.empty {{ text-align:center; color:#484f58; padding:16px; }}
  h2 {{ font-size:13px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px; margin:28px 0 8px; }}
</style>
</head>
<body>
  <h1>Trading Bot Dashboard</h1>
  <div class="sub">Mode: {mode_label} &middot; auto-refreshes every 5s &middot; this tab is read-only, it never places orders</div>
  <div class="badges">{_badge(heartbeat_text, heartbeat_color)}{status_badges}</div>

  <div class="stats">
    <div class="stat"><div class="label">Balance</div><div class="value">{_fmt_money(account.balance_usd)}</div></div>
    <div class="stat"><div class="label">Total Exposure</div><div class="value">{_fmt_money(account.total_exposure_usd)}</div></div>
    <div class="stat"><div class="label">Realized PnL Today</div><div class="value">{_fmt_money(store.get_daily_pnl())}</div></div>
    <div class="stat"><div class="label">Open Positions</div><div class="value">{len(positions)}</div></div>
  </div>

  <h2>Positions ({len(positions)})</h2>
  <table>
    <tr><th>Ticker</th><th>Side</th><th>Qty</th><th>Avg Price</th><th>Strategy</th><th>Cost Basis</th></tr>
    {positions_rows}
  </table>

  <h2>Open Orders ({len(open_orders)})</h2>
  <table>
    <tr><th>Ticker</th><th>Side</th><th>Action</th><th>Count</th><th>Price</th><th>Status</th></tr>
    {orders_rows}
  </table>

  <h2>Recent Decisions (last {len(decisions)})</h2>
  <table>
    <tr><th></th><th>Strategy</th><th>Ticker</th><th>Action</th><th>Edge</th><th>Conf</th><th>Reasoning</th></tr>
    {decisions_html}
  </table>
</body>
</html>"""


def run_server(adapter: ExchangeAdapter, store: StateStore, risk_manager: RiskManager, logs: BotLogs,
               port: int = 8765, poll_interval_seconds: int = 60) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = _render_html(adapter, store, risk_manager, logs, poll_interval_seconds).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass  # silence per-request console logging

    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"Dashboard running at http://127.0.0.1:{port} -- press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
