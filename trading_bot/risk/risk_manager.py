"""Risk management, enforced in code before every order - not advisory config.
Every check here can only shrink or reject a signal, never enlarge it. The
execution engine MUST call check(...) before every single order and skip the
order entirely on any veto. This is the one place all six risk requirements
from the spec live: per-trade cap ($ and %), total exposure cap, daily loss
halt, per-market cap, per-strategy cap, and the kill switch.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import KILL_SWITCH_PATH, Config
from ..exchanges.paper_broker import PaperBroker
from ..models import TradeSignal


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    adjusted_contracts: int = 0


class RiskManager:
    def __init__(self, cfg: Config, broker: PaperBroker):
        self.cfg = cfg
        self.broker = broker

    def kill_switch_engaged(self) -> bool:
        return KILL_SWITCH_PATH.exists()

    def check(self, signal: TradeSignal, day_one_mode: bool = False) -> RiskVerdict:
        state = self.broker.state
        risk = self.cfg.risk

        if self.kill_switch_engaged():
            return RiskVerdict(False, "kill switch engaged (KILL_SWITCH file present) - no new trades")

        if state.daily_pnl_pct <= -risk.daily_loss_limit_pct:
            return RiskVerdict(
                False,
                f"daily loss limit hit: {state.daily_pnl_pct:.1%} <= -{risk.daily_loss_limit_pct:.1%}, halting new trades",
            )

        equity = state.equity
        max_position_abs = risk.day_one_max_position_abs if day_one_mode else risk.max_position_abs
        max_position_dollars = min(max_position_abs, equity * risk.max_position_pct)

        proposed_dollars = signal.size_contracts * signal.limit_price_cents / 100.0
        contracts = signal.size_contracts
        if proposed_dollars > max_position_dollars:
            contracts = max(0, int(max_position_dollars * 100 / signal.limit_price_cents))
            if contracts <= 0:
                return RiskVerdict(False, f"position size ${proposed_dollars:.2f} exceeds per-trade cap ${max_position_dollars:.2f}, and reduced size rounds to 0 contracts")

        trade_dollars = contracts * signal.limit_price_cents / 100.0

        total_exposure = state.unrealized_exposure
        if total_exposure + trade_dollars > equity * risk.max_total_exposure_pct:
            return RiskVerdict(
                False,
                f"would breach max total exposure: ${total_exposure:.2f} + ${trade_dollars:.2f} > "
                f"{risk.max_total_exposure_pct:.0%} of ${equity:.2f} equity",
            )

        market_exposure = self.broker.exposure_for_market(signal.ticker)
        if market_exposure + trade_dollars > equity * risk.max_exposure_per_market_pct:
            return RiskVerdict(
                False,
                f"would breach per-market cap on {signal.ticker}: ${market_exposure:.2f} + ${trade_dollars:.2f} > "
                f"{risk.max_exposure_per_market_pct:.0%} of equity",
            )

        strategy_exposure = self.broker.exposure_for_strategy(signal.strategy)
        if strategy_exposure + trade_dollars > equity * risk.max_exposure_per_strategy_pct:
            return RiskVerdict(
                False,
                f"would breach per-strategy cap on {signal.strategy}: ${strategy_exposure:.2f} + ${trade_dollars:.2f} > "
                f"{risk.max_exposure_per_strategy_pct:.0%} of equity",
            )

        return RiskVerdict(True, "within all risk limits", adjusted_contracts=contracts)

    @staticmethod
    def engage_kill_switch(flatten: bool = False) -> None:
        KILL_SWITCH_PATH.write_text(f"engaged, flatten_requested={flatten}\n")

    @staticmethod
    def disengage_kill_switch() -> None:
        if KILL_SWITCH_PATH.exists():
            KILL_SWITCH_PATH.unlink()
