"""
Independent validation layer (Quant v0.8).

Receives a candidate *without* access to the optimization / hypothesis-search
history used to create it. Evaluation data must be disjoint from research data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from .data import Series
from .dsl import Strategy
from .backtest import run_backtest
from .validate import evaluate_robustness, split_series
from .comparison import robustness_score


def series_fingerprint(series: Series) -> str:
    if not series.bars:
        return "empty"
    b0, b1 = series.bars[0], series.bars[-1]
    raw = f"{series.symbol}:{series.timeframe}:{len(series.bars)}:{b0.ts}:{b1.ts}:{b0.close}:{b1.close}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def assert_disjoint(research: Series, independent: Series) -> None:
    """Reject evaluation if independent window overlaps research timestamps."""
    if not research.bars or not independent.bars:
        return
    r_ts = {b.ts for b in research.bars}
    i_ts = {b.ts for b in independent.bars}
    overlap = r_ts & i_ts
    if overlap:
        raise ValueError(
            f"independent validation data overlaps research data ({len(overlap)} timestamps) — leakage blocked"
        )
    # also block identical fingerprint
    if series_fingerprint(research) == series_fingerprint(independent) and len(research.bars) == len(independent.bars):
        raise ValueError("independent series fingerprint matches research series — leakage blocked")


@dataclass
class IndependentReviewResult:
    candidate_fingerprint: str
    research_data_id: str
    independent_data_id: str
    metrics: Dict[str, Any]
    robustness: Dict[str, Any]
    leakage_checked: bool = True
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class IndependentValidator:
    """
    Stateless reviewer: only strategy + independent series.
    Must not receive hypothesis optimization history.
    """

    def review(
        self,
        strategy: Strategy,
        independent_series: Series,
        *,
        research_series: Optional[Series] = None,
        research_data_id: str = "",
    ) -> IndependentReviewResult:
        if research_series is not None:
            assert_disjoint(research_series, independent_series)
            research_data_id = research_data_id or series_fingerprint(research_series)

        ind_id = series_fingerprint(independent_series)
        bt = run_backtest(independent_series, strategy)
        train, test = split_series(independent_series, 0.7)
        oos = run_backtest(test, strategy) if len(test.bars) >= 15 else bt
        rob = evaluate_robustness(independent_series, strategy, n_folds=3, mc_sims=40)

        metrics = {
            "sharpe": oos.sharpe,
            "oos_sharpe": oos.sharpe,
            "backtest_sharpe": bt.sharpe,
            "max_drawdown": oos.max_drawdown,
            "profit_factor": oos.profit_factor,
            "expectancy": (oos.total_return / oos.trades) if oos.trades else 0.0,
            "trades": oos.trades,
            "total_return": oos.total_return,
            "win_rate": oos.win_rate,
        }
        robustness = {
            "passed": rob.passed,
            "walk_forward_sharpe": rob.walk_forward_mean_sharpe,
            "monte_carlo_p5": rob.monte_carlo_p5_return,
            "monte_carlo_median": rob.monte_carlo_median_return,
            "reasons": rob.reasons,
        }
        from .trial import fingerprint_strategy
        return IndependentReviewResult(
            candidate_fingerprint=fingerprint_strategy(strategy.spec.to_dict()),
            research_data_id=research_data_id or "unspecified",
            independent_data_id=ind_id,
            metrics=metrics,
            robustness=robustness,
            notes=["evaluated without hypothesis search history"],
        )
