"""
Live market-data ingestion for shadow validation.

Accepts pushed bars/ticks (from any external feed process). No broker trading API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .data import Bar
from .market_data import MarketDataStore


@dataclass
class FeedHealth:
    symbol: str
    last_ts: float = 0.0
    bars_received: int = 0
    duplicates: int = 0
    gaps: int = 0
    reconnects: int = 0
    last_error: str = ""


class ShadowMarketFeed:
    """
    In-process feed. External collectors push bars via `on_bar`.
    Validates timestamps, detects duplicates/gaps, supports reconnect marks.
    """

    def __init__(
        self,
        store: Optional[MarketDataStore] = None,
        *,
        expected_step: float = 1.0,
        max_future_skew_s: float = 5.0,
    ):
        self.store = store
        self.expected_step = expected_step
        self.max_future_skew_s = max_future_skew_s
        self.health: Dict[str, FeedHealth] = {}
        self._last_ts: Dict[str, float] = {}
        self._listeners: List[Callable[[str, Bar], None]] = []

    def subscribe(self, callback: Callable[[str, Bar], None]) -> None:
        self._listeners.append(callback)

    def mark_reconnect(self, symbol: str) -> None:
        h = self.health.setdefault(symbol, FeedHealth(symbol=symbol))
        h.reconnects += 1

    def on_bar(self, symbol: str, bar: Bar, timeframe: str = "1m") -> Tuple[bool, str]:
        """
        Ingest one bar. Returns (accepted, reason).
        Rejects future-dated bars beyond skew (anti look-ahead / clock skew).
        """
        now = time.time()
        h = self.health.setdefault(symbol, FeedHealth(symbol=symbol))
        # timestamp validation
        if bar.ts > now + self.max_future_skew_s:
            h.last_error = "future_timestamp"
            return False, "future_timestamp"
        last = self._last_ts.get(symbol)
        if last is not None:
            if bar.ts < last:
                # out of order — reject to avoid leakage/reorder bugs
                h.last_error = "out_of_order"
                return False, "out_of_order"
            if abs(bar.ts - last) < 1e-9:
                h.duplicates += 1
                h.last_error = "duplicate"
                return False, "duplicate"
            if bar.ts - last > self.expected_step * 1.5:
                h.gaps += 1
        self._last_ts[symbol] = bar.ts
        h.last_ts = bar.ts
        h.bars_received += 1
        h.last_error = ""
        if self.store is not None:
            from .data import Series
            self.store.ingest_series(Series(symbol, timeframe, [bar]), source="shadow_feed")
        for cb in self._listeners:
            cb(symbol, bar)
        return True, "ok"

    def push_price(self, symbol: str, price: float, ts: Optional[float] = None, timeframe: str = "1m") -> Tuple[bool, str]:
        ts = float(ts if ts is not None else time.time())
        bar = Bar(ts=ts, open=price, high=price, low=price, close=price, volume=0.0)
        return self.on_bar(symbol, bar, timeframe=timeframe)
