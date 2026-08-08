"""
QuantResearchLab — orchestrates discovery, evaluation, evolution, ranking.

Never claims to predict prices; surfaces historical robustness evidence only.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .data import Series, synthetic_ohlcv, load_csv
from .dsl import Strategy, parse_strategy
from .backtest import run_backtest, run_many, BacktestResult
from .evolve import seed_population, evolve_generation
from .validate import evaluate_robustness, RobustnessReport
from .optimize import rank_results, scalarize, pareto_front
from .regime import detect_regimes, regime_summary
from .knowledge import StrategyKnowledgeBase
from .explain import explain_result


DISCLAIMER = (
    "PEAR Quant Research Lab does not predict future prices. "
    "All outputs are historical robustness assessments only."
)


@dataclass
class LabReport:
    candidates_evaluated: int
    survivors: List[Dict[str, Any]] = field(default_factory=list)
    rejected: int = 0
    rankings: List[Dict[str, Any]] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "candidates_evaluated": self.candidates_evaluated,
            "survivors": self.survivors,
            "rejected": self.rejected,
            "rankings": self.rankings,
            "disclaimer": self.disclaimer,
        }


class QuantResearchLab:
    def __init__(self, kb_path: Optional[Path] = None, max_workers: int = 4):
        self.kb = StrategyKnowledgeBase(path=kb_path)
        self.max_workers = max_workers
        self.series_cache: Dict[str, Series] = {}

    def load_series(self, path: Optional[Path] = None, **synthetic_kw) -> Series:
        if path:
            s = load_csv(Path(path))
        else:
            s = synthetic_ohlcv(**synthetic_kw)
        self.series_cache[f"{s.symbol}:{s.timeframe}"] = s
        return s

    def evaluate_candidates(
        self,
        series: Series,
        strategies: List[Strategy],
        *,
        parallel: bool = True,
    ) -> List[BacktestResult]:
        if not parallel or len(strategies) < 4:
            return run_many(series, strategies)
        results: List[BacktestResult] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futs = {pool.submit(run_backtest, series, s): s for s in strategies}
            for fut in as_completed(futs):
                results.append(fut.result())
        return results

    def research(
        self,
        series: Optional[Series] = None,
        *,
        population_size: int = 40,
        generations: int = 3,
        seed: int = 11,
    ) -> LabReport:
        series = series or self.load_series(n=400, seed=seed)
        regimes = regime_summary(detect_regimes(series))
        pop = seed_population(size=population_size, seed=seed)
        all_results: List[BacktestResult] = []

        for gen in range(generations):
            results = self.evaluate_candidates(series, pop)
            all_results.extend(results)
            fitness = [scalarize(r) for r in results]
            pop = evolve_generation(pop, fitness, seed=seed + gen)

        # final evaluate unique-ish by name
        by_name = {r.strategy_name: r for r in all_results}
        final_list = list(by_name.values())
        survivors = []
        rejected = 0
        robust_map: Dict[str, RobustnessReport] = {}
        for r in final_list:
            # rebuild strategy from params is approximate — use name match from last pop
            strat = next((s for s in pop if s.name == r.strategy_name), None)
            if strat is None:
                strat = parse_strategy({
                    "name": r.strategy_name,
                    "params": r.params,
                })
            rob = evaluate_robustness(series, strat)
            robust_map[r.strategy_name] = rob
            if rob.passed:
                survivors.append({
                    "strategy": r.strategy_name,
                    "metrics": {
                        "return": r.total_return,
                        "max_drawdown": r.max_drawdown,
                        "sharpe": r.sharpe,
                        "win_rate": r.win_rate,
                        "trades": r.trades,
                    },
                    "robustness": rob.to_dict(),
                    "params": r.params,
                    "regimes": regimes,
                })
                self.kb.add(
                    r.strategy_name,
                    symbol=series.symbol,
                    timeframe=series.timeframe,
                    metrics={
                        "return": r.total_return,
                        "sharpe": r.sharpe,
                        "max_drawdown": r.max_drawdown,
                    },
                    regimes=regimes,
                    passed=True,
                    params=r.params,
                )
            else:
                rejected += 1

        ranked = rank_results(final_list)
        rankings = [
            {
                "score": sc,
                "strategy": r.strategy_name,
                "return": r.total_return,
                "sharpe": r.sharpe,
                "max_drawdown": r.max_drawdown,
                "passed_robustness": robust_map.get(r.strategy_name, RobustnessReport(
                    r.strategy_name, 0, 0, 0, 0, 0, 0, False
                )).passed,
            }
            for sc, r in ranked[:20]
        ]

        return LabReport(
            candidates_evaluated=len(final_list),
            survivors=sorted(survivors, key=lambda x: -x["metrics"]["sharpe"]),
            rejected=rejected,
            rankings=rankings,
        )

    def explain(self, series: Series, strategy: Strategy) -> str:
        result = run_backtest(series, strategy)
        rob = evaluate_robustness(series, strategy)
        return explain_result(result, rob)

    def recommend(self, strategy_name: str) -> Dict[str, Any]:
        return self.kb.recommend_conditions(strategy_name)
