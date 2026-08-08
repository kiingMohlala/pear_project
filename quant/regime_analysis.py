"""Regime labeling and performance aggregation for multi-market research."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from .data import Series, Bar
from .regime import detect_regimes, regime_summary


def classify_volatility(closes: List[float], lookback: int = 20) -> str:
    if len(closes) < lookback + 1:
        return "unknown"
    rets = []
    for i in range(len(closes) - lookback, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if not rets:
        return "unknown"
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5
    if vol > 0.02:
        return "high_vol"
    if vol < 0.008:
        return "low_vol"
    return "mid_vol"


def classify_activity(volumes: List[float], lookback: int = 20) -> str:
    if len(volumes) < lookback:
        return "unknown"
    window = volumes[-lookback:]
    avg = sum(window) / len(window)
    if avg <= 0:
        return "unknown"
    last = window[-1]
    if last > avg * 1.5:
        return "high_activity"
    if last < avg * 0.5:
        return "low_activity"
    return "normal_activity"


def bar_regimes(series: Series, lookback: int = 20) -> List[Dict[str, str]]:
    labs = detect_regimes(series, lookback=lookback)
    closes = series.closes()
    volumes = [b.volume for b in series.bars]
    out = []
    for i, lab in enumerate(labs):
        out.append({
            "trend": lab.regime if lab.regime in ("trend_up", "trend_down", "range", "high_vol") else lab.regime,
            "volatility": classify_volatility(closes[: i + 1], lookback=min(lookback, max(5, i))),
            "activity": classify_activity(volumes[: i + 1], lookback=min(lookback, max(5, i))),
        })
    return out


def aggregate_regime_pnl(trade_log: List[Dict[str, Any]], key: str = "regime_exit") -> Dict[str, Dict[str, float]]:
    """Aggregate PnL and counts by regime label on trades."""
    stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "n": 0.0, "wins": 0.0})
    for tr in trade_log:
        if tr.get("pnl") is None:
            continue
        reg = str(tr.get(key) or tr.get("regime") or "unknown")
        stats[reg]["pnl"] += float(tr["pnl"])
        stats[reg]["n"] += 1
        if float(tr["pnl"]) >= 0:
            stats[reg]["wins"] += 1
    for reg, s in stats.items():
        s["avg_pnl"] = s["pnl"] / s["n"] if s["n"] else 0.0
        s["win_rate"] = s["wins"] / s["n"] if s["n"] else 0.0
    return dict(stats)


def score_regime_bucket(stats: Dict[str, float], min_n: int = 5) -> str:
    """Label strong/moderate/weak/poor given sample size gate."""
    n = int(stats.get("n") or 0)
    if n < min_n:
        return "insufficient_sample"
    avg = float(stats.get("avg_pnl") or 0)
    wr = float(stats.get("win_rate") or 0)
    if avg > 0.01 and wr >= 0.45:
        return "strong"
    if avg > 0 and wr >= 0.4:
        return "moderate"
    if avg > -0.005:
        return "weak"
    return "poor"
