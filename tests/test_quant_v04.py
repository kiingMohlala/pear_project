"""Quant v0.4 research intelligence tests."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.research_lab import ResearchLab
from quant.experiment import ExperimentRecord, Disposition, new_experiment
from quant.dsl import parse_strategy
from quant.data import synthetic_ohlcv
from quant.analysis import classify_failure, parameter_stability
from quant.trial import fingerprint_strategy


def test_experiment_seal_immutable():
    spec = {"name": "sma_cross", "params": {"fast": 5, "slow": 20}}
    exp = new_experiment(spec, market="SYN", timeframe="1d", dataset_id="abc")
    exp.backtest = {"sharpe": 1.0}
    exp.disposition = Disposition.SURVIVED
    exp.seal()
    assert exp.sealed and exp.verify_integrity()
    try:
        exp.seal()
        assert False
    except RuntimeError:
        pass
    # tamper
    exp.backtest["sharpe"] = 9.0
    assert exp.verify_integrity() is False


def test_run_experiment_and_queries():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        series = synthetic_ohlcv(n=200, seed=2)
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 6, "slow": 22}})
        exp = lab.run_experiment(s, series)
        assert exp.sealed
        assert exp.strategy_fingerprint == fingerprint_strategy(s.spec.to_dict())
        assert lab.memory.get(exp.id).id == exp.id
        hist = lab.research_history()
        assert any(h["id"] == exp.id for h in hist)
        report = lab.report(exp.id)
        assert "Research report" in report
        assert "does not predict" in report.lower() or "historical" in report.lower()
        assert isinstance(lab.failure_patterns(), list)
        assert lab.market_summary("SYN")["experiments"] >= 1


def test_similar_and_family():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        series = synthetic_ohlcv(n=160, seed=3)
        for fast in (5, 8):
            s = parse_strategy({"name": "sma_cross", "params": {"fast": fast, "slow": 25}})
            lab.run_experiment(s, series)
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 25}})
        sim = lab.similar_experiments(s, limit=10)
        assert len(sim) >= 1
        fam = lab.best_conditions("sma")
        assert isinstance(fam, list)
        fr = lab.family_report("sma")
        assert "Family insight" in fr


def test_no_overwrite_sealed():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        series = synthetic_ohlcv(n=120, seed=4)
        s = parse_strategy({"name": "sma_cross", "params": {"fast": 4, "slow": 15}})
        exp = lab.run_experiment(s, series)
        try:
            lab.memory.add(exp)
            # same id sealed — should fail
            assert False
        except RuntimeError:
            pass


def test_parameter_stability_helper():
    specs = [
        new_experiment({"name": "sma_a", "params": {"fast": 5, "slow": 20}}, market="X", timeframe="1d", dataset_id="1"),
        new_experiment({"name": "sma_b", "params": {"fast": 6, "slow": 22}}, market="X", timeframe="1d", dataset_id="2"),
    ]
    for e in specs:
        e.parameters = e.strategy_spec["params"]
    st = parameter_stability(specs)
    assert st["n"] == 2


if __name__ == "__main__":
    test_experiment_seal_immutable()
    print("  ✓ seal/integrity")
    test_run_experiment_and_queries()
    print("  ✓ experiment+queries")
    test_similar_and_family()
    print("  ✓ similar/family")
    test_no_overwrite_sealed()
    print("  ✓ no overwrite")
    test_parameter_stability_helper()
    print("  ✓ stability")
    print("All quant v0.4 tests passed.")
