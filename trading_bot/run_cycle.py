#!/usr/bin/env python3
"""Entrypoint: run exactly one poll->evaluate->trade->log cycle, then exit.
Call this repeatedly - from a shell while-loop for continuous local running,
or from a scheduled Routine for hands-off operation. Keeping it one-shot
(rather than an internal sleep loop) makes it trivial to run from cron,
systemd timers, or an external scheduler with zero code changes.
"""
from __future__ import annotations

import json
import logging
import sys

from trading_bot.config import load_config
from trading_bot.engine.execution_engine import EngineContext, run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    cfg = load_config()
    missing = cfg.missing_keys()
    if missing:
        print(f"Config incomplete, cannot start: missing {missing}", file=sys.stderr)
        print("Fill in trading_bot/.env (see trading_bot/.env.example).", file=sys.stderr)
        return 1

    ctx = EngineContext(cfg)
    summary = run_cycle(ctx)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
