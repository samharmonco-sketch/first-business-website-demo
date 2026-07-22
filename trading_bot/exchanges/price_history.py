"""Rolling in-process/on-disk price history per market ticker, built up one
poll cycle at a time. Used by the momentum strategy for recent price action
and by the mispricing strategy to estimate realized volatility once enough
samples exist. Kept intentionally simple (a JSON file of recent points) since
its only job is "remember the last N snapshots" across cycles.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from ..config import DATA_DIR

HISTORY_PATH = DATA_DIR / "price_history.json"
MAX_POINTS_PER_TICKER = 200


class PriceHistory:
    def __init__(self):
        self._data: dict[str, list[list[float]]] = defaultdict(list)  # ticker -> [[ts, implied_prob], ...]
        if HISTORY_PATH.exists():
            try:
                raw = json.loads(HISTORY_PATH.read_text())
                self._data.update({k: v for k, v in raw.items()})
            except (json.JSONDecodeError, OSError):
                pass

    def record(self, ticker: str, implied_prob: float) -> None:
        points = self._data[ticker]
        points.append([time.time(), implied_prob])
        if len(points) > MAX_POINTS_PER_TICKER:
            del points[: len(points) - MAX_POINTS_PER_TICKER]

    def recent(self, ticker: str, n: int = 20) -> list[list[float]]:
        return self._data.get(ticker, [])[-n:]

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(self._data))
