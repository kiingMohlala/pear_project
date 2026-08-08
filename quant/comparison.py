"""Comparative ranking across markets, timeframes, regimes — not by raw return alone."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


MIN_SAMPLE_TRADES = 5
MIN_SAMPLE_CELLS = 2


def robustness_score(metrics: Dict[str, Any], divergence_level: str = "MEDIUM") -> float:
    """Higher is better. Penalize drawdown and high divergence."""
    sharpe = float(metrics.get("sharpe") or 0)
    dd = float(metrics.get("max_drawdown") or 0)
    trades = float(metrics.get("trades") or 0)
    pf = float(metrics.get("profit_factor") or 0)
    ret = float(metrics.get("total_return") or 0)
    # primary: risk-adjusted, not raw return
    score = 0.45 * max(-2.0, min(3.0, sharpe)) / 3.0
    score += 0.25 * max(0.0, 1.0 - dd)
    score += 0.15 * min(1.0, trades / 20.0)
    score += 0.10 * min(2.0, pf) / 2.0
    score += 0.05 * max(-1.0, min(1.0, ret))  # weak weight on return
    if divergence_level == "HIGH":
        score -= 0.35
    elif divergence_level == "MEDIUM":
        score -= 0.1
    return score


@dataclass
class CellResult:
    fingerprint: str
    strategy_name: str
    market: str
    timeframe: str
    metrics: Dict[str, Any]
    divergence_level: str = "UNKNOWN"
    regime_stats: Dict[str, Any] = field(default_factory=dict)
    trades: int = 0
    trial_id: str = ""

    def score(self) -> float:
        return robustness_score(self.metrics, self.divergence_level)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["robustness_score"] = self.score()
        return d


def rank_cells(cells: List[CellResult], min_trades: int = MIN_SAMPLE_TRADES) -> List[CellResult]:
    eligible = [c for c in cells if int(c.metrics.get("trades") or c.trades or 0) >= min_trades]
    pool = eligible if eligible else cells  # fall back but report sample issues upstream
    return sorted(pool, key=lambda c: -c.score())


def rank_markets(cells: List[CellResult], min_cells: int = MIN_SAMPLE_CELLS) -> List[Dict[str, Any]]:
    by_m: Dict[str, List[CellResult]] = {}
    for c in cells:
        by_m.setdefault(c.market, []).append(c)
    rows = []
    for m, group in by_m.items():
        scores = [g.score() for g in group]
        avg = sum(scores) / len(scores) if scores else 0.0
        rows.append({
            "market": m,
            "n_cells": len(group),
            "avg_robustness": avg,
            "sufficient_sample": len(group) >= min_cells,
            "best_timeframe": max(group, key=lambda x: x.score()).timeframe if group else None,
        })
    rows.sort(key=lambda r: (-r["sufficient_sample"], -r["avg_robustness"]))
    return rows


def rank_timeframes(cells: List[CellResult], min_cells: int = MIN_SAMPLE_CELLS) -> List[Dict[str, Any]]:
    by_t: Dict[str, List[CellResult]] = {}
    for c in cells:
        by_t.setdefault(c.timeframe, []).append(c)
    rows = []
    for tf, group in by_t.items():
        scores = [g.score() for g in group]
        avg = sum(scores) / len(scores) if scores else 0.0
        rows.append({
            "timeframe": tf,
            "n_cells": len(group),
            "avg_robustness": avg,
            "sufficient_sample": len(group) >= min_cells,
        })
    rows.sort(key=lambda r: (-r["sufficient_sample"], -r["avg_robustness"]))
    return rows


def consistent_vs_unstable(cells: List[CellResult]) -> Dict[str, Any]:
    if len(cells) < 2:
        return {"consistent": False, "reason": "insufficient cells", "score_range": 0.0}
    scores = [c.score() for c in cells]
    spread = max(scores) - min(scores)
    return {
        "consistent": spread < 0.25,
        "score_range": spread,
        "mean": sum(scores) / len(scores),
        "n": len(cells),
    }


def comparative_report(cells: List[CellResult], min_trades: int = MIN_SAMPLE_TRADES) -> str:
    ranked = rank_cells(cells, min_trades=min_trades)
    markets = rank_markets(cells)
    tfs = rank_timeframes(cells)
    stab = consistent_vs_unstable(cells)
    insufficient = sum(1 for c in cells if int(c.metrics.get("trades") or 0) < min_trades)

    # regime rollup
    regime_labels: Dict[str, List[str]] = {}
    for c in cells:
        for reg, st in (c.regime_stats or {}).items():
            from .regime_analysis import score_regime_bucket
            label = score_regime_bucket(st if isinstance(st, dict) else {"n": 0}, min_n=min_trades)
            regime_labels.setdefault(reg, []).append(label)

    def majority(labels: List[str]) -> str:
        if not labels:
            return "unknown"
        from collections import Counter
        return Counter(labels).most_common(1)[0][0]

    best = ranked[0] if ranked else None
    worst = ranked[-1] if ranked else None

    lines = [
        "# Comparative multi-market research report",
        "",
        "_Historical robustness only — not a prediction of future returns._",
        "_Ranking prioritizes risk-adjusted quality, not raw return._",
        "",
        f"## Sample size",
        f"- Cells: {len(cells)}",
        f"- Below min trades ({min_trades}): {insufficient}",
        f"- Consistency: {stab}",
        "",
        "## BEST CONDITIONS",
    ]
    if best and int(best.metrics.get("trades") or 0) >= min_trades:
        lines.append(
            f"- {best.strategy_name} on {best.market} {best.timeframe} "
            f"(robustness={best.score():.3f}, sharpe={best.metrics.get('sharpe')}, "
            f"div={best.divergence_level})"
        )
    else:
        lines.append("- insufficient sample to declare superior conditions")

    lines += ["", "## BEST MARKETS"]
    for m in markets[:5]:
        flag = "" if m["sufficient_sample"] else " [insufficient cells]"
        lines.append(f"- {m['market']}: avg_robustness={m['avg_robustness']:.3f} n={m['n_cells']}{flag}")

    lines += ["", "## BEST TIMEFRAMES"]
    for t in tfs[:5]:
        flag = "" if t["sufficient_sample"] else " [insufficient cells]"
        lines.append(f"- {t['timeframe']}: avg_robustness={t['avg_robustness']:.3f} n={t['n_cells']}{flag}")

    lines += ["", "## WORST CONDITIONS"]
    if worst:
        lines.append(
            f"- {worst.strategy_name} on {worst.market} {worst.timeframe} "
            f"(robustness={worst.score():.3f}, div={worst.divergence_level})"
        )

    lines += ["", "## REGIME SUMMARY"]
    for reg, labs in sorted(regime_labels.items()):
        lines.append(f"- {reg}: {majority(labs)}")

    lines += [
        "",
        "## ROBUSTNESS",
        f"- Mean score range across cells: {stab.get('score_range', 0):.3f}",
        f"- Declared consistent: {stab.get('consistent')}",
        "",
        "## CONFIDENCE",
        "- High only when min sample sizes met and divergence not HIGH.",
        f"- Cells meeting trade minimum: {len(cells) - insufficient}/{len(cells)}",
        "",
        "## KNOWN FAILURE MODES",
        "- HIGH backtest/shadow divergence",
        "- insufficient trades for ranking",
        "- unstable score spread across markets/TFs",
        "",
        "No capital allocation. No real orders. Candidates remained frozen.",
    ]
    return "\n".join(lines)
