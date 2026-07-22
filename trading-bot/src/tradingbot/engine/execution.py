"""The autonomous execution loop.

Each cycle:
  1. Settle any open positions whose market has resolved (see settlement.py
     -- strategies only ever open positions, this is the other half).
  2. Check kill switch / daily halt (still logs the cycle even if halted).
  3. Fetch current account state and market snapshot.
  4. Evaluate every enabled strategy against every relevant market.
  5. Log every evaluation (trade AND no-trade) with full reasoning/inputs.
  6. For actionable signals, run them through the RiskManager -- the only
     path by which an order can reach the exchange adapter.
  7. Place approved orders, log the result, update state.
  8. Log an account snapshot (balance/exposure/PnL) for observability.

This module has zero exchange-specific or strategy-specific logic --
new strategies (registry.py) and new exchanges (adapters/) plug in without
touching this file.
"""
from __future__ import annotations

import signal
import time
import uuid

from ..adapters.base import ExchangeAdapter
from ..config import AppConfig
from ..logging_setup import BotLogs
from ..models import AccountState, Market, OrderAction, OrderRequest, Side, SignalAction
from ..risk.manager import RiskManager
from ..state_store import StateStore
from ..strategies.base import Strategy
from ..strategies.sports_ai import SportsAIStrategy
from .settlement import settle_expired_positions


class ExecutionEngine:
    def __init__(self, config: AppConfig, adapter: ExchangeAdapter, strategies: list[Strategy],
                 risk_manager: RiskManager, store: StateStore, logs: BotLogs):
        self.config = config
        self.adapter = adapter
        self.strategies = strategies
        self.risk_manager = risk_manager
        self.store = store
        self.logs = logs
        self._stop_requested = False

    def request_stop(self, *_args) -> None:
        self.logs.logger.info("Stop requested -- will exit after current cycle completes.")
        self._stop_requested = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        self.logs.logger.info(
            f"Starting execution engine: mode={self.config.mode}, "
            f"poll_interval={self.config.polling.interval_seconds}s, "
            f"strategies={[s.name for s in self.strategies]}"
        )
        while not self._stop_requested:
            try:
                self.run_cycle()
            except Exception as exc:
                self.logs.logger.exception(f"Unhandled error in poll cycle: {exc}")
            for _ in range(self.config.polling.interval_seconds):
                if self._stop_requested:
                    break
                time.sleep(1)
        self.logs.logger.info("Execution engine stopped.")

    def run_cycle(self) -> dict:
        cycle_id = str(uuid.uuid4())[:8]
        self.logs.logger.info(f"[cycle {cycle_id}] starting poll cycle")

        # Settlement is orthogonal to the kill switch: an already-open
        # position resolving isn't a new trade, and letting it settle frees
        # up exposure/balance regardless of whether new trades are halted.
        settled = settle_expired_positions(self.adapter, self.store, self.logs)
        if settled:
            self.logs.logger.info(f"[cycle {cycle_id}] settled {settled} expired position(s)")

        if self.risk_manager.kill_switch.is_active():
            self.logs.log_decision(
                cycle_id=cycle_id, strategy_name="*", ticker="*", action="halt",
                size_usd=0.0, confidence=0.0, edge=0.0,
                reasoning="kill switch is engaged; skipping strategy evaluation entirely",
                inputs={}, traded=False, reject_reason="kill switch engaged",
            )
            self.logs.logger.warning(f"[cycle {cycle_id}] kill switch engaged -- no evaluation this cycle")
            return {"cycle_id": cycle_id, "decisions": 0, "trades": 0, "halted": True}

        account_state = self.adapter.get_account_state()
        markets = self.adapter.get_markets(self.config.polling.crypto_series_tickers,
                                            limit=self.config.polling.max_markets_per_cycle)
        self.logs.logger.info(f"[cycle {cycle_id}] fetched {len(markets)} markets, "
                               f"balance=${account_state.balance_usd:.2f}, "
                               f"exposure=${account_state.total_exposure_usd:.2f}, "
                               f"day_one_active={self.risk_manager.day_one_active}")

        decisions = 0
        trades = 0

        for strategy in self.strategies:
            if isinstance(strategy, SportsAIStrategy):
                signals = strategy.evaluate_events(self.config.polling.sports_series_tickers)
                for signal in signals:
                    decisions += 1
                    traded = self._handle_signal(cycle_id, signal, account_state)
                    if traded:
                        trades += 1
                        account_state = self.adapter.get_account_state()
                continue

            for market in markets:
                signal = strategy.evaluate(market, account_state)
                decisions += 1
                traded = self._handle_signal(cycle_id, signal, account_state)
                if traded:
                    trades += 1
                    account_state = self.adapter.get_account_state()

        self.logs.log_account(
            balance_usd=account_state.balance_usd,
            total_exposure_usd=account_state.total_exposure_usd,
            realized_pnl_today_usd=self.store.get_daily_pnl(),
            num_positions=len(account_state.positions),
        )
        self.logs.logger.info(f"[cycle {cycle_id}] complete: {decisions} decisions, {trades} trades")
        return {"cycle_id": cycle_id, "decisions": decisions, "trades": trades, "halted": False}

    def _handle_signal(self, cycle_id: str, signal, account_state: AccountState) -> bool:
        if not signal.is_actionable:
            self.logs.log_decision(
                cycle_id=cycle_id, strategy_name=signal.strategy_name, ticker=signal.ticker,
                action=signal.action.value, size_usd=signal.size_usd, confidence=signal.confidence,
                edge=signal.edge, reasoning=signal.reasoning, inputs=signal.inputs, traded=False,
            )
            return False

        decision = self.risk_manager.evaluate(signal, account_state)
        if not decision.approved:
            self.logs.log_decision(
                cycle_id=cycle_id, strategy_name=signal.strategy_name, ticker=signal.ticker,
                action=signal.action.value, size_usd=signal.size_usd, confidence=signal.confidence,
                edge=signal.edge, reasoning=signal.reasoning, inputs=signal.inputs, traded=False,
                reject_reason=decision.reason,
            )
            return False

        order_request = self._build_order_request(signal, decision.approved_size_usd)
        if order_request is None:
            self.logs.log_decision(
                cycle_id=cycle_id, strategy_name=signal.strategy_name, ticker=signal.ticker,
                action=signal.action.value, size_usd=signal.size_usd, confidence=signal.confidence,
                edge=signal.edge, reasoning=signal.reasoning, inputs=signal.inputs, traded=False,
                reject_reason="approved size too small to buy at least 1 contract at current price",
            )
            return False

        result = self.adapter.place_order(order_request)
        self.logs.log_order(
            event_type="order_placed", strategy_name=signal.strategy_name, ticker=order_request.ticker,
            side=order_request.side.value, action=order_request.action.value, count=order_request.count,
            price=order_request.limit_price, status=result.status.value, reason=result.reason,
            exchange_order_id=result.exchange_order_id,
        )
        self.logs.log_decision(
            cycle_id=cycle_id, strategy_name=signal.strategy_name, ticker=signal.ticker,
            action=signal.action.value, size_usd=decision.approved_size_usd, confidence=signal.confidence,
            edge=signal.edge, reasoning=signal.reasoning, inputs=signal.inputs,
            traded=result.status.value in ("filled", "partially_filled", "pending"),
        )

        traded = result.status.value in ("filled", "partially_filled", "pending")
        if traded and self.risk_manager.day_one_active:
            self.risk_manager.mark_day_one_trade_executed()
            self.logs.logger.info(
                f"[cycle {cycle_id}] day-one mode: first trade executed on {order_request.ticker} "
                f"-- day-one permissive thresholds now disabled for future cycles"
            )
        return traded

    @staticmethod
    def _build_order_request(signal, approved_size_usd: float) -> OrderRequest | None:
        if signal.action == SignalAction.BUY_YES:
            side = Side.YES
            price = signal.inputs.get("yes_ask") or signal.inputs.get("price") or 0.5
        elif signal.action == SignalAction.BUY_NO:
            side = Side.NO
            price = signal.inputs.get("no_ask") or (1 - (signal.inputs.get("yes_bid") or 0.5))
        else:
            return None

        price = max(0.01, min(0.99, float(price)))
        count = int(approved_size_usd // price)
        if count < 1:
            return None

        return OrderRequest(
            ticker=signal.ticker, side=side, action=OrderAction.BUY, count=count,
            limit_price=price, strategy_name=signal.strategy_name,
        )
