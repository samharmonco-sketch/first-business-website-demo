"""Structured JSON-lines logging. One append-only file per event stream so the
dashboard and CLI can tail them independently: decisions (every signal, trade
or no-trade, with reasoning), orders (every placed/filled/rejected order),
and account snapshots (balance/PnL over time)."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path

from .config import LOGS_DIR

_lock = threading.Lock()

DECISIONS_LOG = LOGS_DIR / "decisions.jsonl"
ORDERS_LOG = LOGS_DIR / "orders.jsonl"
ACCOUNT_LOG = LOGS_DIR / "account.jsonl"
CYCLES_LOG = LOGS_DIR / "cycles.jsonl"


def _default(obj):
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return str(obj)


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with open(path, "a") as f:
            f.write(json.dumps(record, default=_default) + "\n")


def log_decision(record: dict) -> None:
    _append(DECISIONS_LOG, record)


def log_order(record: dict) -> None:
    _append(ORDERS_LOG, record)


def log_account(record: dict) -> None:
    _append(ACCOUNT_LOG, record)


def log_cycle(record: dict) -> None:
    _append(CYCLES_LOG, record)


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        lines = f.readlines()
    if limit:
        lines = lines[-limit:]
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
