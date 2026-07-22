"""SQLite-backed persistent state: positions, orders, daily PnL, bot flags.

This is the single source of truth for account state in paper mode, and a
local mirror/cache of exchange state in live mode (always reconciled against
the exchange on startup and after each fill in live mode).
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    avg_price REAL NOT NULL,
    PRIMARY KEY (ticker, side, strategy_name)
);

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    exchange_order_id TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    count INTEGER NOT NULL,
    limit_price REAL NOT NULL,
    status TEXT NOT NULL,
    filled_count INTEGER NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    trading_day TEXT PRIMARY KEY,
    realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
    halted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_flags (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS balance (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance_usd REAL NOT NULL
);
"""


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class StateStore:
    def __init__(self, db_path: Path, starting_bankroll_usd: float):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        cur = self._conn.execute("SELECT balance_usd FROM balance WHERE id = 1")
        if cur.fetchone() is None:
            self._conn.execute("INSERT INTO balance (id, balance_usd) VALUES (1, ?)", (starting_bankroll_usd,))
        self._conn.commit()

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        finally:
            cur.close()

    # -- Balance -----------------------------------------------------
    def get_balance(self) -> float:
        cur = self._conn.execute("SELECT balance_usd FROM balance WHERE id = 1")
        return cur.fetchone()["balance_usd"]

    def adjust_balance(self, delta_usd: float) -> None:
        with self._cursor() as cur:
            cur.execute("UPDATE balance SET balance_usd = balance_usd + ? WHERE id = 1", (delta_usd,))

    # -- Positions -----------------------------------------------------
    def get_positions(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM positions WHERE quantity != 0")
        return [dict(r) for r in cur.fetchall()]

    def get_position(self, ticker: str, side: str, strategy_name: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT * FROM positions WHERE ticker=? AND side=? AND strategy_name=?",
            (ticker, side, strategy_name),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def upsert_position(self, ticker: str, side: str, strategy_name: str, quantity_delta: int, fill_price: float) -> None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT quantity, avg_price FROM positions WHERE ticker=? AND side=? AND strategy_name=?",
                (ticker, side, strategy_name),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO positions (ticker, side, strategy_name, quantity, avg_price) VALUES (?,?,?,?,?)",
                    (ticker, side, strategy_name, quantity_delta, fill_price),
                )
            else:
                old_qty, old_avg = row["quantity"], row["avg_price"]
                new_qty = old_qty + quantity_delta
                if new_qty == 0:
                    new_avg = 0.0
                elif old_qty >= 0 and quantity_delta > 0:
                    new_avg = ((old_qty * old_avg) + (quantity_delta * fill_price)) / new_qty
                else:
                    new_avg = old_avg
                cur.execute(
                    "UPDATE positions SET quantity=?, avg_price=? WHERE ticker=? AND side=? AND strategy_name=?",
                    (new_qty, new_avg, ticker, side, strategy_name),
                )

    # -- Orders -----------------------------------------------------
    def record_order(self, *, client_order_id: str, exchange_order_id: str | None, ticker: str,
                      side: str, action: str, strategy_name: str, count: int, limit_price: float,
                      status: str, filled_count: int = 0, avg_fill_price: float | None = None) -> None:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO orders (client_order_id, exchange_order_id, ticker, side, action,
                       strategy_name, count, limit_price, status, filled_count, avg_fill_price,
                       created_ts, updated_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(client_order_id) DO UPDATE SET
                       status=excluded.status, filled_count=excluded.filled_count,
                       avg_fill_price=excluded.avg_fill_price, updated_ts=excluded.updated_ts""",
                (client_order_id, exchange_order_id, ticker, side, action, strategy_name, count,
                 limit_price, status, filled_count, avg_fill_price, now, now),
            )

    def get_open_orders(self) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM orders WHERE status IN ('pending','partially_filled')")
        return [dict(r) for r in cur.fetchall()]

    # -- Daily PnL / risk halt -----------------------------------------------------
    def add_realized_pnl(self, amount_usd: float) -> float:
        day = _today_key()
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO daily_pnl (trading_day, realized_pnl_usd) VALUES (?, 0.0) "
                "ON CONFLICT(trading_day) DO NOTHING",
                (day,),
            )
            cur.execute(
                "UPDATE daily_pnl SET realized_pnl_usd = realized_pnl_usd + ? WHERE trading_day=?",
                (amount_usd, day),
            )
            cur.execute("SELECT realized_pnl_usd FROM daily_pnl WHERE trading_day=?", (day,))
            return cur.fetchone()["realized_pnl_usd"]

    def get_daily_pnl(self) -> float:
        day = _today_key()
        cur = self._conn.execute("SELECT realized_pnl_usd FROM daily_pnl WHERE trading_day=?", (day,))
        row = cur.fetchone()
        return row["realized_pnl_usd"] if row else 0.0

    def is_daily_halted(self) -> bool:
        day = _today_key()
        cur = self._conn.execute("SELECT halted FROM daily_pnl WHERE trading_day=?", (day,))
        row = cur.fetchone()
        return bool(row["halted"]) if row else False

    def set_daily_halt(self, halted: bool = True) -> None:
        day = _today_key()
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO daily_pnl (trading_day, realized_pnl_usd, halted) VALUES (?, 0.0, ?) "
                "ON CONFLICT(trading_day) DO UPDATE SET halted=excluded.halted",
                (day, int(halted)),
            )

    # -- Bot flags (e.g. day-one mode completion) -----------------------------------------------------
    def get_flag(self, key: str, default: str | None = None) -> str | None:
        cur = self._conn.execute("SELECT value FROM bot_flags WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO bot_flags (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def close(self) -> None:
        self._conn.close()
