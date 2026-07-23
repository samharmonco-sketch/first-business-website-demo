"""Config loading: all secrets/limits come from env vars / trading_bot/.env, never hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).resolve().parent
DATA_DIR = BOT_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
KILL_SWITCH_PATH = BOT_DIR.parent / "KILL_SWITCH"

load_dotenv(BOT_DIR / ".env")


def _float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 0.05
    max_position_abs: float = 500.0
    max_total_exposure_pct: float = 0.50
    daily_loss_limit_pct: float = 0.15
    max_exposure_per_market_pct: float = 0.10
    max_exposure_per_strategy_pct: float = 0.30
    max_exposure_per_underlying_pct: float = 0.15
    day_one_max_position_abs: float = 50.0
    day_one_min_edge: float = 0.03
    day_one_mode_enabled: bool = True
    take_profit_pct: float = 0.50
    stop_loss_pct: float = 0.40


@dataclass(frozen=True)
class KalshiConfig:
    api_key_id: str
    private_key_path: str
    base_url: str


@dataclass(frozen=True)
class Config:
    mode: str  # "paper" | "live"
    poll_interval_seconds: int
    paper_starting_bankroll: float
    risk: RiskConfig
    kalshi: KalshiConfig
    odds_api_key: str
    odds_api_base_url: str
    anthropic_api_key: str
    anthropic_model: str

    @property
    def is_paper(self) -> bool:
        return self.mode != "live"

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.kalshi.api_key_id:
            missing.append("KALSHI_API_KEY_ID")
        if not Path(self.kalshi.private_key_path).exists():
            missing.append(f"KALSHI private key file at {self.kalshi.private_key_path}")
        if not self.odds_api_key:
            missing.append("ODDS_API_KEY")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing


def load_config() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    risk = RiskConfig(
        max_position_pct=_float("MAX_POSITION_PCT", 0.05),
        max_position_abs=_float("MAX_POSITION_ABS", 500.0),
        max_total_exposure_pct=_float("MAX_TOTAL_EXPOSURE_PCT", 0.50),
        daily_loss_limit_pct=_float("DAILY_LOSS_LIMIT_PCT", 0.15),
        max_exposure_per_market_pct=_float("MAX_EXPOSURE_PER_MARKET_PCT", 0.10),
        max_exposure_per_strategy_pct=_float("MAX_EXPOSURE_PER_STRATEGY_PCT", 0.30),
        max_exposure_per_underlying_pct=_float("MAX_EXPOSURE_PER_UNDERLYING_PCT", 0.15),
        day_one_max_position_abs=_float("DAY_ONE_MAX_POSITION_ABS", 50.0),
        day_one_min_edge=_float("DAY_ONE_MIN_EDGE", 0.03),
        day_one_mode_enabled=_bool("DAY_ONE_MODE_ENABLED", True),
        take_profit_pct=_float("TAKE_PROFIT_PCT", 0.50),
        stop_loss_pct=_float("STOP_LOSS_PCT", 0.40),
    )

    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", str(DATA_DIR / "kalshi_private_key.pem"))
    if not os.path.isabs(private_key_path):
        private_key_path = str(BOT_DIR.parent / private_key_path)

    kalshi = KalshiConfig(
        api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        private_key_path=private_key_path,
        base_url=os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"),
    )

    return Config(
        mode=os.getenv("TRADING_MODE", "paper").strip().lower(),
        poll_interval_seconds=int(_float("POLL_INTERVAL_SECONDS", 60)),
        paper_starting_bankroll=_float("PAPER_STARTING_BANKROLL", 10000.0),
        risk=risk,
        kalshi=kalshi,
        odds_api_key=os.getenv("ODDS_API_KEY", ""),
        odds_api_base_url=os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
    )
