"""Shared data structures passed between adapters, strategies, risk manager, and engine."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    YES = "yes"
    NO = "no"


class OrderAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class SignalAction(str, Enum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"
    HOLD = "hold"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELED = "canceled"


@dataclass
class Market:
    ticker: str
    series_ticker: str
    title: str
    yes_bid: float          # implied probability-ish price, cents/100 -> dollars (0-1)
    yes_ask: float
    no_bid: float
    no_ask: float
    volume: float
    close_ts: float          # unix seconds when market closes/expires
    status: str = "active"
    raw: dict = field(default_factory=dict)

    @property
    def yes_mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def implied_yes_probability(self) -> float:
        return self.yes_mid

    @property
    def seconds_to_close(self) -> float:
        return max(0.0, self.close_ts - time.time())


@dataclass
class Position:
    ticker: str
    side: Side
    quantity: int          # number of contracts
    avg_price: float        # dollars per contract (0-1)
    strategy_name: str = ""

    @property
    def cost_basis_usd(self) -> float:
        return self.quantity * self.avg_price


@dataclass
class AccountState:
    balance_usd: float
    positions: list[Position] = field(default_factory=list)
    open_orders: list["Order"] = field(default_factory=list)
    realized_pnl_today_usd: float = 0.0
    total_exposure_usd: float = 0.0

    def exposure_for_market(self, ticker: str) -> float:
        return sum(p.cost_basis_usd for p in self.positions if p.ticker == ticker)

    def exposure_for_strategy(self, strategy_name: str) -> float:
        return sum(p.cost_basis_usd for p in self.positions if p.strategy_name == strategy_name)


@dataclass
class Signal:
    """Output of a strategy evaluating one market."""
    strategy_name: str
    ticker: str
    action: SignalAction
    size_usd: float
    confidence: float          # 0-1
    edge: float                # modeled_prob - implied_prob (signed, magnitude = edge size)
    reasoning: str
    inputs: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_actionable(self) -> bool:
        return self.action != SignalAction.HOLD and self.size_usd > 0


@dataclass
class OrderRequest:
    ticker: str
    side: Side
    action: OrderAction
    count: int
    limit_price: float          # dollars per contract, 0-1
    strategy_name: str
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class OrderResult:
    client_order_id: str
    exchange_order_id: str | None
    status: OrderStatus
    filled_count: int
    avg_fill_price: float | None
    ticker: str
    side: Side
    action: OrderAction
    reason: str = ""
    raw: dict = field(default_factory=dict)
