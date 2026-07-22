"""Structured JSON-lines logging.

Three separate append-only streams:
  decisions.jsonl  -- every signal evaluated, trade or no-trade, with reasoning
  orders.jsonl      -- every order placed/filled/rejected
  account.jsonl     -- account balance / PnL snapshots over time

Each line is a single JSON object with a timestamp and event type, so the
dashboard (and any external tool) can tail/parse them trivially.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        event = {"ts": time.time(), **event}
        line = json.dumps(event, default=str)
        with self._lock:
            with open(self.path, "a") as f:
                f.write(line + "\n")

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "r") as f:
            lines = f.readlines()
        out = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


class BotLogs:
    """Bundles the three JSONL streams plus a standard python logger."""

    def __init__(self, log_dir: Path, decisions_file: str, orders_file: str, account_file: str, level: str = "INFO"):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.decisions = JsonlWriter(log_dir / decisions_file)
        self.orders = JsonlWriter(log_dir / orders_file)
        self.account = JsonlWriter(log_dir / account_file)

        self.logger = logging.getLogger("tradingbot")
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            self.logger.addHandler(handler)

            file_handler = logging.FileHandler(log_dir / "bot.log")
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            self.logger.addHandler(file_handler)

    def log_decision(self, *, cycle_id: str, strategy_name: str, ticker: str, action: str,
                      size_usd: float, confidence: float, edge: float, reasoning: str,
                      inputs: dict, traded: bool, reject_reason: str = "") -> None:
        self.decisions.write({
            "event": "decision",
            "cycle_id": cycle_id,
            "strategy": strategy_name,
            "ticker": ticker,
            "action": action,
            "size_usd": size_usd,
            "confidence": confidence,
            "edge": edge,
            "reasoning": reasoning,
            "inputs": inputs,
            "traded": traded,
            "reject_reason": reject_reason,
        })

    def log_order(self, *, event_type: str, strategy_name: str, ticker: str, side: str,
                  action: str, count: int, price: float, status: str, reason: str = "",
                  exchange_order_id: str | None = None) -> None:
        self.orders.write({
            "event": event_type,
            "strategy": strategy_name,
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "price": price,
            "status": status,
            "reason": reason,
            "exchange_order_id": exchange_order_id,
        })

    def log_account(self, *, balance_usd: float, total_exposure_usd: float,
                     realized_pnl_today_usd: float, num_positions: int) -> None:
        self.account.write({
            "event": "account_snapshot",
            "balance_usd": balance_usd,
            "total_exposure_usd": total_exposure_usd,
            "realized_pnl_today_usd": realized_pnl_today_usd,
            "num_positions": num_positions,
        })
