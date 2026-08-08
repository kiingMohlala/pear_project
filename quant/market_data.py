"""Persistent market-data ingestion with missing-data detection."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .data import Bar, Series, normalize_bars, synthetic_ohlcv, load_csv


@dataclass
class GapReport:
    symbol: str
    timeframe: str
    missing_bars: int
    expected_bars: int
    coverage: float
    gaps: List[Tuple[float, float]]  # (from_ts, to_ts)


class MarketDataStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else Path.home() / ".pear" / "quant_market.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT, timeframe TEXT, ts REAL,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY (symbol, timeframe, ts)
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS ingest_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, timeframe TEXT, n_bars INTEGER, ts REAL, source TEXT
            )"""
        )
        self._conn.commit()

    def ingest_series(self, series: Series, source: str = "csv") -> int:
        n = 0
        for b in series.bars:
            self._conn.execute(
                "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
                (series.symbol, series.timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume),
            )
            n += 1
        self._conn.execute(
            "INSERT INTO ingest_log (symbol, timeframe, n_bars, ts, source) VALUES (?,?,?,?,?)",
            (series.symbol, series.timeframe, n, time.time(), source),
        )
        self._conn.commit()
        return n

    def ingest_csv(self, path: Path, symbol: Optional[str] = None, timeframe: str = "1d") -> int:
        series = load_csv(path, symbol=symbol, timeframe=timeframe)
        return self.ingest_series(series, source=str(path))

    def ingest_synthetic(self, **kw) -> Series:
        series = synthetic_ohlcv(**kw)
        self.ingest_series(series, source="synthetic")
        return series

    def load(self, symbol: str, timeframe: str = "1d", start: Optional[float] = None, end: Optional[float] = None) -> Series:
        sql = "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol=? AND timeframe=?"
        args: list = [symbol, timeframe]
        if start is not None:
            sql += " AND ts>=?"
            args.append(start)
        if end is not None:
            sql += " AND ts<=?"
            args.append(end)
        sql += " ORDER BY ts"
        rows = self._conn.execute(sql, tuple(args)).fetchall()
        bars = [Bar(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
        return Series(symbol=symbol, timeframe=timeframe, bars=bars)

    def detect_gaps(self, symbol: str, timeframe: str = "1d", expected_step: float = 1.0) -> GapReport:
        series = self.load(symbol, timeframe)
        gaps = []
        missing = 0
        for i in range(1, len(series.bars)):
            dt = series.bars[i].ts - series.bars[i - 1].ts
            if dt > expected_step * 1.5:
                # approximate missing count
                miss = int(dt / expected_step) - 1
                missing += max(0, miss)
                gaps.append((series.bars[i - 1].ts, series.bars[i].ts))
        expected = len(series.bars) + missing
        coverage = len(series.bars) / expected if expected else 1.0
        return GapReport(symbol, timeframe, missing, expected, coverage, gaps[:50])
