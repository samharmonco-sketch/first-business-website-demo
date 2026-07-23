"""Shared data shapes passed between adapters, strategies, risk manager, and engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketCategory(str, Enum):
    CRYPTO = "crypto"
    SPORTS = "sports"
    OTHER = "other"


class Side(str, Enum):
    YES = "yes"
    NO = "no"


class OrderAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class MarketSnapshot:
    ticker: str
    title: str
    category: MarketCategory
    yes_bid: float  # cents, 0-100
    yes_ask: float
    no_bid: float
    no_ask: float
    last_price: Optional[float]
    volume: int
    close_time: Optional[str]
    series_ticker: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def implied_yes_prob(self) -> float:
        """Mid-market implied probability of YES, 0-1."""
        if self.yes_bid is not None and self.yes_ask is not None:
            return ((self.yes_bid + self.yes_ask) / 2.0) / 100.0
        return (self.last_price or 50.0) / 100.0


@dataclass
class TradeSignal:
    strategy: str
    ticker: str
    action: OrderAction
    side: Side
    size_contracts: int
    limit_price_cents: float
    confidence: float  # 0-1
    reasoning: str
    edge: float = 0.0  # model_prob - market implied prob, signed
    extra: dict = field(default_factory=dict)


@dataclass
class NoTradeDecision:
    strategy: str
    ticker: str
    reasoning: str
    evaluated_at: str = field(default_factory=now_iso)


@dataclass
class Position:
    ticker: str
    side: Side
    contracts: int
    avg_price_cents: float
    strategy: str
    opened_at: str = field(default_factory=now_iso)
    confidence: float = 0.0  # size-weighted average of the strategy's stated confidence across fills

    def cost_basis(self) -> float:
        return self.contracts * self.avg_price_cents / 100.0


@dataclass
class Order:
    order_id: str
    ticker: str
    action: OrderAction
    side: Side
    contracts: int
    price_cents: float
    strategy: str
    status: str  # "filled" | "rejected" | "pending"
    reason: str = ""
    created_at: str = field(default_factory=now_iso)


@dataclass
class AccountState:
    bankroll: float
    starting_bankroll: float
    cash: float
    positions: dict  # ticker -> Position
    realized_pnl: float = 0.0
    day_start_bankroll: float = 0.0
    day_start_date: str = ""

    @property
    def unrealized_exposure(self) -> float:
        return sum(p.cost_basis() for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.unrealized_exposure

    @property
    def daily_pnl_pct(self) -> float:
        if self.day_start_bankroll <= 0:
            return 0.0
        return (self.equity - self.day_start_bankroll) / self.day_start_bankroll
