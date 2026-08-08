"""Historical market data ingestion and normalization."""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Bar:
    ts: float  # unix or ordinal
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Series:
    symbol: str
    timeframe: str
    bars: List[Bar] = field(default_factory=list)

    def closes(self) -> List[float]:
        return [b.close for b in self.bars]

    def highs(self) -> List[float]:
        return [b.high for b in self.bars]

    def lows(self) -> List[float]:
        return [b.low for b in self.bars]


def normalize_bars(rows: List[dict], symbol: str = "SYM", timeframe: str = "1d") -> Series:
    bars: List[Bar] = []
    for i, r in enumerate(rows):
        o = float(r.get("open") or r.get("Open") or r.get("close") or r.get("Close") or 0)
        c = float(r.get("close") or r.get("Close") or o)
        h = float(r.get("high") or r.get("High") or max(o, c))
        l = float(r.get("low") or r.get("Low") or min(o, c))
        v = float(r.get("volume") or r.get("Volume") or 0)
        ts = float(r.get("ts") or r.get("timestamp") or i)
        if h < max(o, c):
            h = max(o, c)
        if l > min(o, c):
            l = min(o, c)
        bars.append(Bar(ts=ts, open=o, high=h, low=l, close=c, volume=v))
    return Series(symbol=symbol, timeframe=timeframe, bars=bars)


def load_csv(path: Path, symbol: Optional[str] = None, timeframe: str = "1d") -> Series:
    path = Path(path)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return normalize_bars(rows, symbol=symbol or path.stem.upper(), timeframe=timeframe)


def synthetic_ohlcv(
    n: int = 500,
    start: float = 100.0,
    drift: float = 0.0002,
    vol: float = 0.015,
    seed: int = 42,
    symbol: str = "SYN",
    timeframe: str = "1d",
) -> Series:
    """Deterministic synthetic series for offline tests (not real market data)."""
    rng = random.Random(seed)
    price = start
    rows = []
    for i in range(n):
        ret = drift + vol * (rng.random() * 2 - 1)
        o = price
        c = max(0.01, price * (1 + ret))
        h = max(o, c) * (1 + 0.002 * rng.random())
        l = min(o, c) * (1 - 0.002 * rng.random())
        rows.append({"ts": i, "open": o, "high": h, "low": l, "close": c, "volume": 1000 + rng.random() * 500})
        price = c
    return normalize_bars(rows, symbol=symbol, timeframe=timeframe)


def returns(closes: List[float]) -> List[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] == 0:
            out.append(0.0)
        else:
            out.append((closes[i] - closes[i - 1]) / closes[i - 1])
    return out
