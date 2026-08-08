"""Market/timeframe universe and candidate × market × TF matrix definitions."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    asset_class: str = "crypto"  # crypto | fx | equity
    notes: str = ""


@dataclass(frozen=True)
class TimeframeSpec:
    name: str          # e.g. "15m", "1h"
    bars_per_day: float


@dataclass
class MatrixCell:
    strategy_fingerprint: str
    strategy_name: str
    market: str
    timeframe: str
    bars_per_day: float

    def key(self) -> str:
        return f"{self.strategy_fingerprint}:{self.market}:{self.timeframe}"

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_MARKETS = [
    MarketSpec("BTCUSDT", "crypto"),
    MarketSpec("ETHUSDT", "crypto"),
    MarketSpec("EURUSD", "fx"),
    MarketSpec("SYN", "synthetic"),
]

DEFAULT_TIMEFRAMES = [
    TimeframeSpec("15m", 96.0),
    TimeframeSpec("1h", 24.0),
    TimeframeSpec("1d", 1.0),
]


def build_matrix(
    fingerprints: List[Tuple[str, str]],  # (fingerprint, name)
    markets: Optional[List[MarketSpec]] = None,
    timeframes: Optional[List[TimeframeSpec]] = None,
) -> List[MatrixCell]:
    markets = markets or DEFAULT_MARKETS
    timeframes = timeframes or DEFAULT_TIMEFRAMES
    cells: List[MatrixCell] = []
    for fp, name in fingerprints:
        for m in markets:
            for tf in timeframes:
                cells.append(MatrixCell(
                    strategy_fingerprint=fp,
                    strategy_name=name,
                    market=m.symbol,
                    timeframe=tf.name,
                    bars_per_day=tf.bars_per_day,
                ))
    return cells
