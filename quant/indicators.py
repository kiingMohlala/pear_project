"""Simple indicators for the strategy DSL."""

from __future__ import annotations

from typing import List, Optional


def sma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or not values:
        return out
    k = 2 / (period + 1)
    prev = values[0]
    for i, v in enumerate(values):
        if i == 0:
            prev = v
            out[i] = v
        else:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out


def roc(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        out[i] = 0.0 if base == 0 else (values[i] - base) / base
    return out
