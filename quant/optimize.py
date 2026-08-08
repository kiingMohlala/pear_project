"""Multi-objective ranking: profit, drawdown, stability, risk-adjusted return."""

from __future__ import annotations

from typing import Dict, List, Tuple

from .backtest import BacktestResult


def scalarize(
    result: BacktestResult,
    weights: Dict[str, float] | None = None,
) -> float:
    w = weights or {
        "return": 0.25,
        "drawdown": 0.25,
        "sharpe": 0.30,
        "win_rate": 0.10,
        "stability": 0.10,
    }
    v = result.score_vector
    # normalize-ish
    score = 0.0
    score += w.get("return", 0) * max(-1.0, min(2.0, v["return"]))
    score += w.get("drawdown", 0) * max(-1.0, min(1.0, v["drawdown"]))
    score += w.get("sharpe", 0) * max(-2.0, min(3.0, v["sharpe"])) / 3.0
    score += w.get("win_rate", 0) * v["win_rate"]
    score += w.get("stability", 0) * v["stability"]
    return score


def rank_results(results: List[BacktestResult]) -> List[Tuple[float, BacktestResult]]:
    scored = [(scalarize(r), r) for r in results]
    scored.sort(key=lambda x: -x[0])
    return scored


def pareto_front(results: List[BacktestResult]) -> List[BacktestResult]:
    """Simple 2D Pareto on (return, -drawdown)."""
    front: List[BacktestResult] = []
    for r in results:
        dominated = False
        for o in results:
            if o is r:
                continue
            if o.total_return >= r.total_return and o.max_drawdown <= r.max_drawdown and (
                o.total_return > r.total_return or o.max_drawdown < r.max_drawdown
            ):
                dominated = True
                break
        if not dominated:
            front.append(r)
    return front
