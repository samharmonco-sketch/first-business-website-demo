"""Risk management: the one place trade approval/rejection is decided.

Every signal, regardless of strategy or mode (paper/live), passes through
RiskManager.evaluate() before an order is ever placed. This is enforced in
code, not configuration -- the execution engine has no path to call
adapter.place_order() without going through here first.

Enforced limits:
  - Max position size per trade ($ and % of bankroll)
  - Max total exposure at any time
  - Daily loss limit (halts all new trades for the rest of the trading day)
  - Per-market exposure cap
  - Per-strategy exposure cap
  - Kill switch (file-based flag; halts all new trades immediately)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import BankrollConfig, DayOneConfig, RiskConfig
from ..models import AccountState, Signal
from ..state_store import StateStore


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    approved_size_usd: float = 0.0


class KillSwitch:
    """A file-based kill switch so it can be toggled from outside the
    running process (e.g. `python -m tradingbot.main kill`) without
    needing IPC into the running loop."""

    def __init__(self, flag_path: Path):
        self.flag_path = flag_path

    def is_active(self) -> bool:
        return self.flag_path.exists()

    def engage(self) -> None:
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.flag_path.write_text("kill switch engaged\n")

    def disengage(self) -> None:
        if self.flag_path.exists():
            self.flag_path.unlink()


class RiskManager:
    def __init__(self, bankroll: BankrollConfig, day_one: DayOneConfig, risk_cfg: RiskConfig,
                 store: StateStore, root_dir: Path):
        self.bankroll = bankroll
        self.day_one = day_one
        self.store = store
        self.kill_switch = KillSwitch(root_dir / risk_cfg.kill_switch_file)

    # -- Day-one mode -----------------------------------------------------
    @property
    def day_one_active(self) -> bool:
        if not self.day_one.enabled:
            return False
        if self.day_one.disable_after_first_trade and self.store.get_flag("day_one_completed") == "true":
            return False
        return True

    def mark_day_one_trade_executed(self) -> None:
        if self.day_one.disable_after_first_trade:
            self.store.set_flag("day_one_completed", "true")

    def thresholds(self) -> tuple[float, float]:
        """Returns (min_edge, min_confidence) for the currently active mode."""
        if self.day_one_active:
            return self.day_one.min_edge_to_trade, self.day_one.min_confidence_to_trade
        return self.bankroll.min_edge_to_trade, self.bankroll.min_confidence_to_trade

    def max_position_usd(self) -> float:
        if self.day_one_active:
            return self.day_one.max_position_usd
        return self.bankroll.max_position_usd

    def max_total_exposure_usd(self) -> float:
        if self.day_one_active:
            return self.day_one.max_total_exposure_usd
        return self.bankroll.max_total_exposure_usd

    # -- Core gate -----------------------------------------------------
    def evaluate(self, signal: Signal, account_state: AccountState) -> RiskDecision:
        if self.kill_switch.is_active():
            return RiskDecision(False, "kill switch engaged: no new trades")

        if self.store.is_daily_halted():
            return RiskDecision(False, "daily loss limit previously hit: trading halted for today")

        daily_pnl = self.store.get_daily_pnl()
        if daily_pnl <= -self.bankroll.daily_loss_limit_usd:
            self.store.set_daily_halt(True)
            return RiskDecision(False, f"daily loss limit reached (${daily_pnl:.2f}): halting new trades")

        if not signal.is_actionable:
            return RiskDecision(False, "signal is HOLD or zero size")

        min_edge, min_confidence = self.thresholds()
        if abs(signal.edge) < min_edge:
            return RiskDecision(False, f"edge {signal.edge:.3f} below minimum {min_edge:.3f}")
        if signal.confidence < min_confidence:
            return RiskDecision(False, f"confidence {signal.confidence:.2f} below minimum {min_confidence:.2f}")

        # Position sizing caps.
        max_per_trade = self.max_position_usd()
        max_pct = self.bankroll.max_position_pct_of_bankroll
        size_usd = min(signal.size_usd, max_per_trade, account_state.balance_usd * max_pct)
        if size_usd <= 0:
            return RiskDecision(False, "computed position size is zero after applying caps")

        # Total exposure cap.
        max_total = self.max_total_exposure_usd()
        if account_state.total_exposure_usd + size_usd > max_total:
            remaining = max_total - account_state.total_exposure_usd
            if remaining <= 0:
                return RiskDecision(False, f"max total exposure (${max_total:.2f}) already reached")
            size_usd = remaining

        # Per-market cap.
        market_exposure = account_state.exposure_for_market(signal.ticker)
        if market_exposure + size_usd > self.bankroll.per_market_exposure_usd:
            remaining = self.bankroll.per_market_exposure_usd - market_exposure
            if remaining <= 0:
                return RiskDecision(False, f"per-market exposure cap (${self.bankroll.per_market_exposure_usd:.2f}) reached for {signal.ticker}")
            size_usd = remaining

        # Per-strategy cap.
        strategy_exposure = account_state.exposure_for_strategy(signal.strategy_name)
        if strategy_exposure + size_usd > self.bankroll.per_strategy_exposure_usd:
            remaining = self.bankroll.per_strategy_exposure_usd - strategy_exposure
            if remaining <= 0:
                return RiskDecision(False, f"per-strategy exposure cap (${self.bankroll.per_strategy_exposure_usd:.2f}) reached for {signal.strategy_name}")
            size_usd = remaining

        # Sufficient balance.
        if size_usd > account_state.balance_usd:
            size_usd = account_state.balance_usd
        if size_usd <= 0:
            return RiskDecision(False, "insufficient balance for any position")

        return RiskDecision(True, "approved", approved_size_usd=round(size_usd, 2))

    def record_fill_pnl(self, realized_pnl_usd: float) -> None:
        new_total = self.store.add_realized_pnl(realized_pnl_usd)
        if new_total <= -self.bankroll.daily_loss_limit_usd:
            self.store.set_daily_halt(True)
