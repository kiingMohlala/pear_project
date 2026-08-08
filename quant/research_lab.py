"""
ResearchLab facade — experiment manager + intelligence queries (v0.4).

Does not place orders or mutate strategies during evaluation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from .data import Series
from .dsl import Strategy
from .backtest import run_backtest
from .validate import evaluate_robustness, split_series
from .regime import detect_regimes, regime_summary
from .experiment import ExperimentRecord, Disposition, new_experiment
from .research_memory import ResearchMemory
from .analysis import classify_failure, backtest_paper_divergence, parameter_stability
from .research_report import generate_report, family_insight
from .trial import fingerprint_strategy


def dataset_id_for(series: Series, source: str = "") -> str:
    raw = f"{series.symbol}:{series.timeframe}:{len(series.bars)}:{source}"
    if series.bars:
        raw += f":{series.bars[0].ts}:{series.bars[-1].ts}:{series.bars[0].close}:{series.bars[-1].close}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ResearchLab:
    def __init__(self, memory_path: Optional[Path] = None):
        self.memory = ResearchMemory(path=memory_path)

    def run_experiment(
        self,
        strategy: Strategy,
        series: Series,
        *,
        source: str = "research",
        paper_metrics: Optional[Dict[str, Any]] = None,
        divergence: Optional[Dict[str, Any]] = None,
        execution: Optional[Dict[str, Any]] = None,
        auto_seal: bool = True,
    ) -> ExperimentRecord:
        """
        Full research evaluation → immutable experiment record.
        """
        spec = strategy.spec.to_dict()
        exp = new_experiment(
            spec,
            market=series.symbol,
            timeframe=series.timeframe,
            dataset_id=dataset_id_for(series, source),
            family=strategy.name.split("_")[0],
        )
        # backtest full series for baseline metrics
        bt = run_backtest(series, strategy)
        exp.backtest = {
            "sharpe": bt.sharpe,
            "max_drawdown": bt.max_drawdown,
            "trades": bt.trades,
            "total_return": bt.total_return,
            "win_rate": bt.win_rate,
            "profit_factor": bt.profit_factor,
        }
        # OOS via split
        train, test = split_series(series, 0.7)
        if len(test.bars) >= 20:
            oos = run_backtest(test, strategy)
            exp.oos = {
                "sharpe": oos.sharpe,
                "max_drawdown": oos.max_drawdown,
                "trades": oos.trades,
                "total_return": oos.total_return,
            }
        rob = evaluate_robustness(series, strategy, n_folds=3, mc_sims=40)
        exp.monte_carlo = {
            "p5": rob.monte_carlo_p5_return,
            "median": rob.monte_carlo_median_return,
            "walk_forward_sharpe": rob.walk_forward_mean_sharpe,
            "passed_robustness": rob.passed,
        }
        exp.regimes = regime_summary(detect_regimes(series))
        if paper_metrics:
            exp.paper = dict(paper_metrics)
        if divergence:
            exp.divergence = dict(divergence)
        elif paper_metrics:
            exp.divergence = backtest_paper_divergence(exp)
        if execution:
            exp.execution = dict(execution)

        exp.failure_reasons = classify_failure(exp)
        if not rob.passed:
            exp.failure_reasons = list(dict.fromkeys(exp.failure_reasons + rob.reasons))
            exp.disposition = Disposition.FAILED
            exp.notes.append("failed robustness gates")
        elif exp.divergence.get("level") == "HIGH":
            exp.disposition = Disposition.RETIRED
            exp.notes.append("high backtest/paper divergence")
        elif paper_metrics and exp.divergence.get("level") in ("LOW", "MEDIUM"):
            exp.disposition = Disposition.SURVIVED
            exp.notes.append("survived research + paper evidence")
        elif rob.passed:
            exp.disposition = Disposition.SURVIVED
            exp.notes.append("passed robustness; no paper leg yet")

        if auto_seal:
            exp.seal()
        self.memory.add(exp)
        return exp

    # ── queries ───────────────────────────────────────────────────

    def similar_experiments(self, strategy: Strategy, **kw) -> List[ExperimentRecord]:
        return self.memory.similar_experiments(
            strategy_fingerprint=fingerprint_strategy(strategy.spec.to_dict()),
            family=strategy.name.split("_")[0],
            **kw,
        )

    def best_conditions(self, strategy_family: str):
        return self.memory.best_conditions(strategy_family)

    def failure_patterns(self):
        return self.memory.failure_patterns()

    def market_summary(self, market: str):
        return self.memory.market_summary(market)

    def research_history(self, limit: int = 50):
        return self.memory.research_history(limit=limit)

    def report(self, experiment_id: str) -> str:
        return generate_report(self.memory.get(experiment_id), self.memory)

    def family_report(self, family: str) -> str:
        return family_insight(family, self.memory)

    def parameter_stability_for_family(self, family: str) -> Dict[str, Any]:
        rows = self.memory.similar_experiments(family=family, limit=100)
        return parameter_stability(rows)
