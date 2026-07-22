"""Paper trading broker: simulates fills against real, live market snapshots
(fetched from the real exchange adapter) without ever sending a real order.
This is the default execution path (config.mode == "paper") and owns the
persisted account state (cash, positions, realized PnL) in data/state.json.

Fill model: a paper BUY fills at the current best ask (yes_ask for YES,
no_ask for NO); a paper SELL fills at the current best bid. This is
intentionally conservative (worse than mid-price) so paper PnL isn't
flattering relative to what a live order would realistically get.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import DATA_DIR
from ..models import AccountState, MarketSnapshot, Order, OrderAction, Position, Side, now_iso

STATE_PATH = DATA_DIR / "state.json"


class PaperBroker:
    def __init__(self, starting_bankroll: float):
        self.starting_bankroll = starting_bankroll
        self.state = self._load_or_init()

    def _load_or_init(self) -> AccountState:
        today = date.today().isoformat()
        if STATE_PATH.exists():
            raw = json.loads(STATE_PATH.read_text())
            positions = {
                t: Position(
                    ticker=p["ticker"],
                    side=Side(p["side"]),
                    contracts=p["contracts"],
                    avg_price_cents=p["avg_price_cents"],
                    strategy=p["strategy"],
                    opened_at=p.get("opened_at", now_iso()),
                )
                for t, p in raw.get("positions", {}).items()
            }
            state = AccountState(
                bankroll=raw.get("bankroll", self.starting_bankroll),
                starting_bankroll=raw.get("starting_bankroll", self.starting_bankroll),
                cash=raw.get("cash", self.starting_bankroll),
                positions=positions,
                realized_pnl=raw.get("realized_pnl", 0.0),
                day_start_bankroll=raw.get("day_start_bankroll", self.starting_bankroll),
                day_start_date=raw.get("day_start_date", today),
            )
        else:
            state = AccountState(
                bankroll=self.starting_bankroll,
                starting_bankroll=self.starting_bankroll,
                cash=self.starting_bankroll,
                positions={},
                realized_pnl=0.0,
                day_start_bankroll=self.starting_bankroll,
                day_start_date=today,
            )
        # Roll the daily-loss-limit window forward if a new day started.
        if state.day_start_date != today:
            state.day_start_date = today
            state.day_start_bankroll = state.equity
        return state

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bankroll": self.state.bankroll,
            "starting_bankroll": self.state.starting_bankroll,
            "cash": self.state.cash,
            "realized_pnl": self.state.realized_pnl,
            "day_start_bankroll": self.state.day_start_bankroll,
            "day_start_date": self.state.day_start_date,
            "positions": {t: asdict(p) for t, p in self.state.positions.items()},
            "updated_at": now_iso(),
        }
        STATE_PATH.write_text(json.dumps(payload, indent=2, default=str))

    def fill_price_cents(self, market: MarketSnapshot, action: OrderAction, side: Side) -> float:
        if side == Side.YES:
            return market.yes_ask if action == OrderAction.BUY else market.yes_bid
        return market.no_ask if action == OrderAction.BUY else market.no_bid

    def execute(
        self, market: MarketSnapshot, action: OrderAction, side: Side, contracts: int, strategy: str
    ) -> Order:
        price_cents = self.fill_price_cents(market, action, side)
        cost = contracts * price_cents / 100.0
        key = f"{market.ticker}:{side.value}"

        if action == OrderAction.BUY:
            if cost > self.state.cash:
                return Order(
                    order_id=str(uuid.uuid4()),
                    ticker=market.ticker,
                    action=action,
                    side=side,
                    contracts=contracts,
                    price_cents=price_cents,
                    strategy=strategy,
                    status="rejected",
                    reason=f"insufficient paper cash: need ${cost:.2f}, have ${self.state.cash:.2f}",
                )
            existing = self.state.positions.get(key)
            if existing:
                total_contracts = existing.contracts + contracts
                existing.avg_price_cents = (
                    existing.avg_price_cents * existing.contracts + price_cents * contracts
                ) / total_contracts
                existing.contracts = total_contracts
            else:
                self.state.positions[key] = Position(
                    ticker=market.ticker,
                    side=side,
                    contracts=contracts,
                    avg_price_cents=price_cents,
                    strategy=strategy,
                )
            self.state.cash -= cost
        else:  # SELL / exit
            existing = self.state.positions.get(key)
            held = existing.contracts if existing else 0
            if not existing or held < contracts:
                return Order(
                    order_id=str(uuid.uuid4()),
                    ticker=market.ticker,
                    action=action,
                    side=side,
                    contracts=contracts,
                    price_cents=price_cents,
                    strategy=strategy,
                    status="rejected",
                    reason=f"cannot sell {contracts} contracts, only hold {held}",
                )
            proceeds = contracts * price_cents / 100.0
            realized = (price_cents - existing.avg_price_cents) * contracts / 100.0
            self.state.cash += proceeds
            self.state.realized_pnl += realized
            existing.contracts -= contracts
            if existing.contracts == 0:
                del self.state.positions[key]

        self.state.bankroll = self.state.equity
        self.save()
        return Order(
            order_id=str(uuid.uuid4()),
            ticker=market.ticker,
            action=action,
            side=side,
            contracts=contracts,
            price_cents=price_cents,
            strategy=strategy,
            status="filled",
        )

    def exposure_for_market(self, ticker: str) -> float:
        return sum(p.cost_basis() for k, p in self.state.positions.items() if k.startswith(f"{ticker}:"))

    def exposure_for_strategy(self, strategy: str) -> float:
        return sum(p.cost_basis() for p in self.state.positions.values() if p.strategy == strategy)
