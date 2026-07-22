# Autonomous Prediction-Market Trading Bot

A long-running, autonomous trading bot for crypto interval prediction
markets (Kalshi first) with a pluggable strategy framework, enforced risk
management, and an AI-driven sports analysis module (Claude via the
Anthropic API).

**Paper trading is the default.** The bot will not place a single real
order until you explicitly set `mode: live` in `config/config.yaml` (or
`TRADING_BOT_MODE=live` in `.env`) *and* provide valid Kalshi API
credentials -- it refuses to start in live mode otherwise.

## Why Kalshi

Kalshi was chosen as the first exchange integration:

- **Regulatory status**: Kalshi is a CFTC-regulated US designated contract
  market. Polymarket operates on-chain and, while widely used, has a
  murkier regulatory position for US users and historically restricted US
  retail access.
- **API usability**: Kalshi's REST API (`/trade-api/v2`) is a straightforward
  authenticated-REST design (RSA-PSS signed requests) with public,
  unauthenticated market-data endpoints (`/markets`, `/markets/{ticker}/orderbook`)
  -- meaning paper trading can run off live data with zero API keys.
  Polymarket's stack is closer to a DEX (on-chain order books, CLOB API,
  wallet/gas considerations), which is more moving parts for a first
  integration.
- **Crypto interval markets**: Kalshi lists dedicated crypto up/down and
  range markets (series like `KXBTC`, `KXETH`) that map directly onto the
  "BTC up or down in the next hour" style market this bot targets.

The exchange integration is isolated behind `adapters/base.py`
(`ExchangeAdapter`), so a Polymarket adapter can be added later without
touching the strategy engine, risk manager, or execution loop.

**Verify against Kalshi's live API reference before trading real money.**
This adapter was built from Kalshi's published API documentation, but this
sandbox's network policy blocked live calls to Kalshi's servers during
development, so the wire format has not been round-tripped against a real
account. Test thoroughly against Kalshi's **demo/sandbox environment**
(`KALSHI_USE_DEMO=true`, the default) before pointing at production.

## Architecture

```
trading-bot/
  config/config.yaml        # all tunables: risk limits, polling, strategy params
  .env / .env.example        # secrets: API keys, mode override
  src/tradingbot/
    config.py                # YAML + env loader, fails fast on bad live-mode config
    models.py                 # Market, Signal, Order, AccountState, Position dataclasses
    state_store.py             # SQLite: positions, orders, daily PnL, bot flags
    logging_setup.py            # JSON-lines logging: decisions / orders / account
    adapters/
      base.py                   # ExchangeAdapter interface
      kalshi.py                  # Kalshi adapter (RSA-PSS auth, market data, orders)
      paper.py                    # Paper trading: simulated fills, real market data
    risk/manager.py               # ALL trade approval logic -- the one non-bypassable gate
    strategies/
      base.py / registry.py        # plugin interface + name->class registry
      dummy_buy.py                  # smoke-test strategy (disabled by default)
      crypto_momentum.py             # momentum / mean-reversion on price history
      mispricing.py                   # implied prob vs lognormal reference-price model
      sports_ai.py                     # AI sports strategy (Claude-backed)
    sports/
      data_sources.py                  # pluggable stats/odds/injury provider interface
      analyzer.py                       # Claude prompt/response, fully audit-logged
    engine/execution.py                 # the autonomous poll/evaluate/risk/trade loop
    dashboard/cli.py                     # status view: positions/orders/decisions/PnL
    main.py                              # CLI: run / once / status / kill / resume
  docker/                                 # Dockerfile + docker-compose.yml
  deploy/trading-bot.service               # systemd unit for bare-metal/VPS deployment
  tests/                                    # risk manager + strategy + smoke tests
```

## Setup

```bash
cd trading-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp .env.example .env
# edit .env: leave KALSHI_* blank for paper mode; fill in ANTHROPIC_API_KEY
# if you want the sports_ai strategy to actually call Claude.
```

Review `config/config.yaml` and set your real bankroll/risk numbers under
`bankroll:` before running anything beyond a smoke test. The shipped
defaults assume a ~$1,500 bankroll, $50 max per trade, $300 max total
exposure, $100 daily loss limit -- **these are enforced in
`risk/manager.py`, not just suggestions.**

## Running

```bash
# One poll cycle, useful for verifying config/connectivity before looping:
python -m tradingbot.main once

# Full autonomous loop (paper mode by default):
python -m tradingbot.main run

# Dashboard: positions, open orders, recent decisions, balance/PnL:
python -m tradingbot.main status

# Kill switch -- stops all new trades immediately:
python -m tradingbot.main kill
python -m tradingbot.main kill --flatten   # also close all open positions
python -m tradingbot.main resume            # re-enable trading
```

The kill switch is a file (`data/KILL_SWITCH` by default) so it can be
toggled from a separate shell while `run` is looping in another process/
container -- the engine checks for it at the start of every cycle.

### First run / "day-one" mode

`day_one.enabled: true` (default) runs a more permissive threshold
(lower min-edge/confidence, smaller max position: $5/trade, $15 total
exposure) on top of the same risk caps, specifically so your first launch
produces a real, small, capped trade instead of an hour of empty logs. It
disables itself automatically the moment one real trade executes
(persisted in `data/state.db`, survives restarts) and steady-state
strategy thresholds take over from then on. If day-one mode runs and finds
truly nothing at or above its (already loose) thresholds, that's visible
directly in `logs/decisions.jsonl` with the specific numeric edge/
confidence that fell short -- not silence.

### Switching to live trading

1. Generate a Kalshi API key pair (`Settings > API Keys` in the Kalshi UI),
   fill in `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in `.env`.
2. Leave `KALSHI_USE_DEMO=true` and set `TRADING_BOT_MODE=live` -- this
   trades against Kalshi's sandbox with fake money but the real signed-
   request code path, so you're testing the actual live-mode wiring.
3. Once you've watched it run correctly against demo, set
   `KALSHI_USE_DEMO=false` and confirm explicitly before your next `run` --
   this is the one step that should never happen without you deliberately
   flipping it.

## Deployment

Deployment-agnostic by design -- pick whichever fits:

- **Docker** (`docker/Dockerfile` + `docker/docker-compose.yml`):
  ```bash
  cd docker && docker compose up -d --build
  ```
  `restart: unless-stopped` brings it back after a crash or host reboot.
  Runs identically on your own machine or any VPS.

- **systemd** (`deploy/trading-bot.service`): for a bare-metal host/VPS
  without Docker. `Restart=on-failure` + `WantedBy=multi-user.target`
  gives crash resilience and reboot survival. See comments in the unit
  file for install steps.

Either way, `data/` (SQLite state + kill switch) and `logs/` (JSON-lines
audit trail) should be on a persistent volume/disk -- they're what let the
bot resume correctly after a restart instead of losing track of positions
or the daily loss halt.

## Observability

Three JSON-lines streams under `logs/`:

- `decisions.jsonl` -- every signal evaluated, trade or no-trade, with the
  strategy's reasoning and numeric inputs (edge, confidence, prices).
- `orders.jsonl` -- every order placed, its status, fill price.
- `account.jsonl` -- balance/exposure/PnL snapshot after every cycle.

Plus `logs/sports_audit.jsonl`: the full prompt sent to Claude and its raw
response for every sports-market evaluation, so AI-driven calls are fully
auditable, not just their resulting trade.

## Adding a new strategy

1. Subclass `Strategy` in a new file under `strategies/`, implement
   `evaluate(market, account_state) -> Signal`.
2. Register it in `strategies/registry.py`'s `STRATEGY_REGISTRY` dict.
3. Add its name + params block to `config/config.yaml`'s `strategies:` section.

The execution engine and risk manager require no changes.

## Adding a second exchange (e.g. Polymarket)

Implement `ExchangeAdapter` (`adapters/base.py`) against the new
exchange's API, following `adapters/kalshi.py` as a template. Strategies,
the risk manager, and the execution engine are exchange-agnostic and
require no changes.

## Known limitations / next steps

- **Kalshi wire format unverified live**: see the note above -- confirm
  against Kalshi's current API reference and the demo environment before
  trusting the adapter with real credentials.
- **sports_ai has no real data source wired in yet**: `StubSportsDataProvider`
  deliberately returns zero events rather than fabricating placeholder
  stats/odds that could be mistaken for real ones. Implement
  `SportsDataProvider` (`sports/data_sources.py`) against a real odds/stats
  API (TheOddsAPI, SportsDataIO, etc.) to activate it.
- **Crypto strike parsing**: `mispricing.py` reads Kalshi's `floor_strike`/
  `cap_strike` fields when present, falling back to regex-parsing the
  market title. Confirm this matches the actual field names Kalshi returns
  for the specific crypto series you trade.
- Momentum/mean-reversion and mispricing are intentionally simple starter
  models (rolling z-score, lognormal digital-option approximation) --
  treat them as a scaffold to improve, not a finished edge.
