"""Walk-forward, out-of-sample, and Monte Carlo robustness tests."""

from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .data import Series, Bar
from .dsl import Strategy
from .backtest import run_backtest, BacktestResult


@dataclass
class RobustnessReport:
    strategy_name: str
    walk_forward_mean_return: float
    walk_forward_mean_sharpe: float
    oos_return: float
    oos_sharpe: float
    monte_carlo_p5_return: float
    monte_carlo_median_return: float
    passed: bool
    reasons: List[str] = field(default_factory=list)
    folds: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def split_series(series: Series, train_frac: float = 0.7) -> tuple:
    n = len(series.bars)
    cut = max(20, int(n * train_frac))
    train = Series(series.symbol, series.timeframe, series.bars[:cut])
    test = Series(series.symbol, series.timeframe, series.bars[cut:])
    return train, test


def walk_forward(series: Series, strategy: Strategy, n_folds: int = 4) -> List[BacktestResult]:
    n = len(series.bars)
    fold_size = n // (n_folds + 1)
    results = []
    for f in range(n_folds):
        train_end = fold_size * (f + 2)
        test_start = fold_size * (f + 1)
        test_end = min(n, test_start + fold_size)
        if test_end - test_start < 15:
            continue
        # evaluate on test window (params assumed fixed for this concept lab)
        window = Series(series.symbol, series.timeframe, series.bars[test_start:test_end])
        results.append(run_backtest(window, strategy))
    return results


def monte_carlo_trade_shuffle(
    result: BacktestResult,
    n_sims: int = 200,
    seed: int = 0,
) -> Dict[str, float]:
    """Shuffle trade returns to assess path dependency (robustness, not prediction)."""
    rets = [t.get("ret", 0.0) for t in result.trade_log if "ret" in t]
    if len(rets) < 3:
        return {"p5": 0.0, "median": 0.0, "p95": 0.0}
    rng = random.Random(seed)
    finals = []
    for _ in range(n_sims):
        shuffled = rets[:]
        rng.shuffle(shuffled)
        eq = 1.0
        for r in shuffled:
            eq *= 1 + r
        finals.append(eq - 1.0)
    finals.sort()
    return {
        "p5": finals[int(0.05 * len(finals))],
        "median": finals[len(finals) // 2],
        "p95": finals[int(0.95 * len(finals))],
    }


def evaluate_robustness(
    series: Series,
    strategy: Strategy,
    *,
    min_oos_sharpe: float = 0.0,
    min_wf_sharpe: float = -0.2,
    max_dd: float = 0.35,
    n_folds: int = 4,
    mc_sims: int = 100,
) -> RobustnessReport:
    train, test = split_series(series)
    oos = run_backtest(test, strategy) if len(test.bars) >= 15 else run_backtest(series, strategy)
    folds = walk_forward(series, strategy, n_folds=n_folds)
    wf_ret = sum(f.total_return for f in folds) / len(folds) if folds else 0.0
    wf_sh = sum(f.sharpe for f in folds) / len(folds) if folds else 0.0
    full = run_backtest(series, strategy)
    mc = monte_carlo_trade_shuffle(full, n_sims=mc_sims)

    reasons = []
    passed = True
    if oos.sharpe < min_oos_sharpe:
        passed = False
        reasons.append(f"oos sharpe {oos.sharpe:.2f} < {min_oos_sharpe}")
    if wf_sh < min_wf_sharpe:
        passed = False
        reasons.append(f"walk-forward sharpe {wf_sh:.2f} < {min_wf_sharpe}")
    if full.max_drawdown > max_dd:
        passed = False
        reasons.append(f"max drawdown {full.max_drawdown:.2%} > {max_dd:.0%}")
    if mc["p5"] < -0.5:
        passed = False
        reasons.append(f"monte carlo p5 return {mc['p5']:.2%} too weak")
    if full.trades < 3:
        passed = False
        reasons.append("too few trades for statistical assessment")

    return RobustnessReport(
        strategy_name=strategy.name,
        walk_forward_mean_return=wf_ret,
        walk_forward_mean_sharpe=wf_sh,
        oos_return=oos.total_return,
        oos_sharpe=oos.sharpe,
        monte_carlo_p5_return=mc["p5"],
        monte_carlo_median_return=mc["median"],
        passed=passed,
        reasons=reasons,
        folds=[f.to_dict() for f in folds],
    )
