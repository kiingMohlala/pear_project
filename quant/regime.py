"""Regime detection: trend / range / high-low volatility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .data import Series, returns
from .indicators import sma


@dataclass
class RegimeLabel:
    index: int
    regime: str  # trend_up | trend_down | range | high_vol | low_vol
    volatility: float
    trend_strength: float


def detect_regimes(series: Series, lookback: int = 20) -> List[RegimeLabel]:
    closes = series.closes()
    rets = returns(closes)
    labels: List[RegimeLabel] = []
    ma = sma(closes, lookback)
    for i in range(len(closes)):
        if i < lookback or ma[i] is None:
            labels.append(RegimeLabel(i, "unknown", 0.0, 0.0))
            continue
        window = rets[max(0, i - lookback):i]
        vol = (sum(r * r for r in window) / len(window)) ** 0.5 if window else 0.0
        trend = (closes[i] - ma[i]) / ma[i] if ma[i] else 0.0
        # vol regime
        if vol > 0.02:
            vol_name = "high_vol"
        elif vol < 0.008:
            vol_name = "low_vol"
        else:
            vol_name = None
        if abs(trend) < 0.01:
            reg = "range"
        elif trend > 0:
            reg = "trend_up"
        else:
            reg = "trend_down"
        if vol_name == "high_vol":
            reg = "high_vol"
        labels.append(RegimeLabel(i, reg, vol, trend))
    return labels


def regime_summary(labels: List[RegimeLabel]) -> dict:
    counts: dict = {}
    for lab in labels:
        counts[lab.regime] = counts.get(lab.regime, 0) + 1
    total = max(1, len(labels))
    return {k: v / total for k, v in counts.items()}
