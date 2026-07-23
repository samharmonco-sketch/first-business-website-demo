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
import time
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
                    confidence=p.get("confidence", 0.0),
                    vol_used=p.get("vol_used", 0.0),
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
        self,
        market: MarketSnapshot,
        action: OrderAction,
        side: Side,
        contracts: int,
        strategy: str,
        confidence: float = 0.0,
        vol_used: float = 0.0,
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
                existing.confidence = (
                    existing.confidence * existing.contracts + confidence * contracts
                ) / total_contracts
                existing.vol_used = (
                    existing.vol_used * existing.contracts + vol_used * contracts
                ) / total_contracts
                existing.contracts = total_contracts
            else:
                self.state.positions[key] = Position(
                    ticker=market.ticker,
                    side=side,
                    contracts=contracts,
                    avg_price_cents=price_cents,
                    strategy=strategy,
                    confidence=confidence,
                    vol_used=vol_used,
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

    @staticmethod
    def underlying_of(ticker: str) -> str:
        """Kalshi tickers are `<event_ticker>-<market_suffix>` (confirmed against
        the live API - e.g. every BTC strike closing at the same time shares one
        event_ticker, and both team-markets of one game share one event_ticker).
        Different strikes/sides of the same event move together, so they're
        correlated risk, not independent - this is the grouping key for that."""
        return ticker.rsplit("-", 1)[0]

    def exposure_for_underlying(self, ticker: str) -> float:
        underlying = self.underlying_of(ticker)
        return sum(p.cost_basis() for p in self.state.positions.values() if self.underlying_of(p.ticker) == underlying)

    def directional_exposure_by_underlying(self) -> dict[str, dict]:
        """Groups every open position by underlying event and nets YES vs NO
        cost basis, for the dashboard's aggregate-exposure view."""
        groups: dict[str, dict] = {}
        for p in self.state.positions.values():
            underlying = self.underlying_of(p.ticker)
            g = groups.setdefault(underlying, {"yes_exposure": 0.0, "no_exposure": 0.0, "strategies": set()})
            if p.side.value == "yes":
                g["yes_exposure"] += p.cost_basis()
            else:
                g["no_exposure"] += p.cost_basis()
            g["strategies"].add(p.strategy)
        for g in groups.values():
            g["net_exposure"] = g["yes_exposure"] - g["no_exposure"]
            g["total_exposure"] = g["yes_exposure"] + g["no_exposure"]
            g["strategies"] = sorted(g["strategies"])
        return groups

    def settle_resolved_positions(self, get_result) -> list[dict]:
        """Kalshi's 15-min/hourly contracts resolve on their own; this is what
        actually credits the win/loss into paper cash/realized_pnl instead of
        leaving filled positions open (and exposure permanently used up)
        forever. get_result(ticker) -> 'yes'/'no'/'' (still open)."""
        settlements = []
        for key, pos in list(self.state.positions.items()):
            time.sleep(0.08)  # pace requests - one Kalshi call per open position, easy to hit rate limits
            try:
                result = get_result(pos.ticker)
            except Exception:  # noqa: BLE001 - a lookup failure just leaves the position open to retry next cycle
                continue
            if result not in ("yes", "no"):
                continue
            payout_cents = 100.0 if pos.side.value == result else 0.0
            proceeds = pos.contracts * payout_cents / 100.0
            realized = proceeds - pos.cost_basis()
            self.state.cash += proceeds
            self.state.realized_pnl += realized
            del self.state.positions[key]
            settlements.append(
                {
                    "ticker": pos.ticker,
                    "side": pos.side.value,
                    "contracts": pos.contracts,
                    "result": result,
                    "strategy": pos.strategy,
                    "realized_pnl": realized,
                    "confidence": pos.confidence,
                    "vol_used": pos.vol_used,
                    "won": realized > 0,
                    "entry_price_cents": pos.avg_price_cents,
                }
            )
        if settlements:
            self.state.bankroll = self.state.equity
            self.save()
        return settlements

    def reset_daily_loss_tracking(self) -> dict:
        """Manually re-baselines the daily-loss-limit window to current
        equity, for an operator explicitly clearing a halt rather than
        waiting for the calendar day to roll over. Returns the before/after
        for an audit log entry - this should never happen silently."""
        before = {"day_start_bankroll": self.state.day_start_bankroll, "day_start_date": self.state.day_start_date}
        self.state.day_start_bankroll = self.state.equity
        self.state.day_start_date = date.today().isoformat()
        self.save()
        return {"before": before, "after": {"day_start_bankroll": self.state.day_start_bankroll, "day_start_date": self.state.day_start_date}}
