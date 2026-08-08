"""Quant Research Lab tests — offline, deterministic."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant import QuantResearchLab, parse_strategy
from quant.data import synthetic_ohlcv
from quant.backtest import run_backtest
from quant.evolve import seed_population, evolve_generation
from quant.validate import evaluate_robustness
from quant.optimize import rank_results, scalarize
from quant.regime import detect_regimes, regime_summary


def test_dsl_and_backtest():
    s = synthetic_ohlcv(n=200, seed=1)
    strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
    r = run_backtest(s, strat)
    assert r.n_bars == 200
    assert r.trades >= 0
    assert "return" in r.score_vector


def test_evolution_and_parallel_eval():
    lab = QuantResearchLab(max_workers=2)
    series = synthetic_ohlcv(n=180, seed=2)
    pop = seed_population(size=12, seed=3)
    results = lab.evaluate_candidates(series, pop, parallel=True)
    assert len(results) == 12
    fit = [scalarize(r) for r in results]
    nxt = evolve_generation(pop, fit, seed=4)
    assert len(nxt) == 12


def test_robustness_gates():
    series = synthetic_ohlcv(n=250, seed=5)
    strat = parse_strategy({"name": "sma_cross", "params": {"fast": 8, "slow": 40}})
    rep = evaluate_robustness(series, strat, n_folds=3, mc_sims=50)
    assert hasattr(rep, "passed")
    assert isinstance(rep.reasons, list)


def test_research_pipeline():
    with tempfile.TemporaryDirectory() as td:
        lab = QuantResearchLab(kb_path=Path(td) / "kb.json", max_workers=2)
        series = lab.load_series(n=220, seed=9)
        report = lab.research(series, population_size=16, generations=2, seed=9)
        assert report.candidates_evaluated > 0
        assert "not predict" in report.disclaimer.lower() or "does not predict" in report.disclaimer.lower()
        assert isinstance(report.rankings, list)
        # ranking prefers quality fields
        if report.rankings:
            assert "sharpe" in report.rankings[0]


def test_regimes_and_explain():
    series = synthetic_ohlcv(n=100, seed=8)
    labs = detect_regimes(series)
    assert len(labs) == 100
    summary = regime_summary(labs)
    assert summary
    lab = QuantResearchLab()
    strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 15}})
    text = lab.explain(series, strat)
    assert "Disclaimer" in text or "historical" in text.lower()


def test_kb_recommend():
    with tempfile.TemporaryDirectory() as td:
        lab = QuantResearchLab(kb_path=Path(td) / "kb.json")
        lab.kb.add("sma_cross", symbol="SYN", timeframe="1d", metrics={"sharpe": 1.2}, regimes={"trend_up": 0.6}, passed=True)
        rec = lab.recommend("sma_cross")
        assert rec["markets"]
        assert "disclaimer" in rec


if __name__ == "__main__":
    test_dsl_and_backtest()
    print("  ✓ dsl/backtest")
    test_evolution_and_parallel_eval()
    print("  ✓ evolve/parallel")
    test_robustness_gates()
    print("  ✓ robustness")
    test_research_pipeline()
    print("  ✓ research")
    test_regimes_and_explain()
    print("  ✓ regime/explain")
    test_kb_recommend()
    print("  ✓ kb")
    print("All quant lab tests passed.")
