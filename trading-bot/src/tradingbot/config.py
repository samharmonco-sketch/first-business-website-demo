"""Config loading: YAML defaults + environment variable overrides for secrets.

Never hardcode API keys or private key material. Everything sensitive comes
from the environment (see .env.example), everything else lives in
config/config.yaml and can be overridden per-deployment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _get_bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class ExchangeConfig:
    name: str
    use_demo: bool
    base_url: str
    demo_base_url: str
    request_timeout_seconds: int
    api_key_id: str = field(default_factory=lambda: os.environ.get("KALSHI_API_KEY_ID", ""))
    private_key_path: str = field(
        default_factory=lambda: os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    )

    @property
    def active_base_url(self) -> str:
        return self.demo_base_url if self.use_demo else self.base_url


@dataclass
class PollingConfig:
    interval_seconds: int
    crypto_series_tickers: list[str]
    sports_series_tickers: list[str]
    max_markets_per_cycle: int


@dataclass
class BankrollConfig:
    starting_bankroll_usd: float
    max_position_usd: float
    max_position_pct_of_bankroll: float
    max_total_exposure_usd: float
    daily_loss_limit_usd: float
    per_market_exposure_usd: float
    per_strategy_exposure_usd: float
    min_edge_to_trade: float
    min_confidence_to_trade: float


@dataclass
class DayOneConfig:
    enabled: bool
    min_edge_to_trade: float
    min_confidence_to_trade: float
    max_position_usd: float
    max_total_exposure_usd: float
    disable_after_first_trade: bool


@dataclass
class ExitsConfig:
    take_profit_pct: float
    stop_loss_pct: float


@dataclass
class RiskConfig:
    kill_switch_file: str
    state_db_path: str


@dataclass
class LoggingConfig:
    log_dir: str
    level: str
    decisions_file: str
    orders_file: str
    account_file: str


@dataclass
class AnthropicConfig:
    model: str
    max_tokens: int
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))


@dataclass
class OddsApiConfig:
    api_key: str = field(default_factory=lambda: os.environ.get("ODDS_API_KEY", ""))


@dataclass
class AppConfig:
    mode: str
    exchange: ExchangeConfig
    polling: PollingConfig
    bankroll: BankrollConfig
    day_one: DayOneConfig
    strategies: dict[str, Any]
    exits: ExitsConfig
    risk: RiskConfig
    logging: LoggingConfig
    anthropic: AnthropicConfig
    odds_api: OddsApiConfig
    root_dir: Path

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def resolve_path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self.root_dir / p)


def load_config(config_path: str | Path | None = None, root_dir: str | Path | None = None) -> AppConfig:
    root = Path(root_dir) if root_dir else Path(__file__).resolve().parents[2]
    cfg_path = Path(config_path) if config_path else root / "config" / "config.yaml"

    with open(cfg_path, "r") as f:
        raw = yaml.safe_load(f)

    mode = os.environ.get("TRADING_BOT_MODE", raw.get("mode", "paper"))

    exchange_raw = raw["exchange"]
    exchange = ExchangeConfig(
        name=exchange_raw["name"],
        use_demo=_get_bool_env("KALSHI_USE_DEMO", exchange_raw["use_demo"]),
        base_url=exchange_raw["base_url"],
        demo_base_url=exchange_raw["demo_base_url"],
        request_timeout_seconds=exchange_raw["request_timeout_seconds"],
    )

    polling = PollingConfig(**raw["polling"])
    bankroll = BankrollConfig(**raw["bankroll"])
    day_one = DayOneConfig(**raw["day_one"])
    exits = ExitsConfig(**raw["exits"])
    risk = RiskConfig(**raw["risk"])
    logging_cfg = LoggingConfig(**raw["logging"])
    anthropic_raw = raw["anthropic"]
    anthropic = AnthropicConfig(model=anthropic_raw["model"], max_tokens=anthropic_raw["max_tokens"])
    odds_api = OddsApiConfig()

    cfg = AppConfig(
        mode=mode,
        exchange=exchange,
        polling=polling,
        bankroll=bankroll,
        day_one=day_one,
        strategies=raw["strategies"],
        exits=exits,
        risk=risk,
        logging=logging_cfg,
        anthropic=anthropic,
        odds_api=odds_api,
        root_dir=root,
    )

    # Fail fast if live mode requested without credentials configured.
    if cfg.is_live:
        if not exchange.api_key_id or not exchange.private_key_path:
            raise RuntimeError(
                "mode=live requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH "
                "to be set in the environment. Refusing to start live trading "
                "without validated credentials."
            )
        if not Path(exchange.private_key_path).exists():
            raise RuntimeError(
                f"KALSHI_PRIVATE_KEY_PATH does not point to an existing file: "
                f"{exchange.private_key_path}"
            )

    return cfg
