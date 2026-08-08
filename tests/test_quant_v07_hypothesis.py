"""Evidence-driven hypothesis tests (Quant v0.7)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.research_lab import ResearchLab
from quant.hypothesis_engine import HypothesisEngine
from quant.hypothesis import HypothesisStatus
from quant.dsl import parse_strategy
from quant.data import synthetic_ohlcv
from quant.trial import fingerprint_strategy


def _seed_memory(lab: ResearchLab, n: int = 4):
    series = synthetic_ohlcv(n=160, seed=1)
    for i, (f, s) in enumerate([(5, 20), (8, 30), (4, 15), (12, 40)]):
        strat = parse_strategy({"name": "sma_cross", "params": {"fast": f, "slow": s}})
        lab.run_experiment(strat, series if i % 2 == 0 else synthetic_ohlcv(n=160, seed=2 + i))


def test_ungrounded_rejected():
    with tempfile.TemporaryDirectory() as td:
        eng = HypothesisEngine(persist_path=Path(td) / "h.json", min_evidence_experiments=2)
        h = eng.reject_ungrounded("Try RSI because it might work.")
        assert h.status == HypothesisStatus.REJECTED_EVIDENCE
        assert "insufficient evidence" in h.explanation.lower() or "REJECTED" in h.explanation


def test_generate_requires_evidence():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        eng = HypothesisEngine(memory=lab.memory, persist_path=Path(td) / "h.json", min_evidence_experiments=2)
        assert eng.generate_from_memory() == []
        _seed_memory(lab)
        hyps = eng.generate_from_memory(family="sma", limit=5)
        assert len(hyps) >= 1
        h = hyps[0]
        assert h.parent_experiments
        assert h.sealed or h.status == HypothesisStatus.REJECTED_EVIDENCE
        text = h.human_readable()
        assert "HYPOTHESIS" in text
        assert "Falsification" in text or "falsification" in text.lower()


def test_spawn_does_not_change_parent_fingerprint():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        _seed_memory(lab)
        eng = HypothesisEngine(memory=lab.memory, persist_path=Path(td) / "h.json")
        hyps = eng.generate_from_memory(family="sma")
        h = next(x for x in hyps if x.status != HypothesisStatus.REJECTED_EVIDENCE)
        parent_fp = h.parent_strategies[0] if h.parent_strategies else ""
        strat = eng.spawn_candidate(h.id)
        child_fp = fingerprint_strategy(strat.spec.to_dict())
        assert child_fp != parent_fp or strat.spec.to_dict() != h.base_strategy_spec
        assert h.child_candidate_id == child_fp
        assert h.status == HypothesisStatus.CANDIDATE_SPAWNED


def test_pipeline_no_shortcut():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        _seed_memory(lab)
        eng = HypothesisEngine(memory=lab.memory, persist_path=Path(td) / "h.json")
        hyps = eng.generate_from_memory(family="sma")
        h = next(x for x in hyps if x.parent_experiments)
        series = synthetic_ohlcv(n=180, seed=9)
        exp = eng.evaluate_candidate_through_pipeline(h.id, series, research=lab)
        assert exp.sealed
        assert exp.id in eng.hypotheses[h.id].child_experiment_ids
        assert eng.hypotheses[h.id].status in (
            HypothesisStatus.FALSIFIED,
            HypothesisStatus.SURVIVED,
            HypothesisStatus.TESTING,
            HypothesisStatus.CANDIDATE_SPAWNED,
        )
        lin = eng.lineage_report(h.id)
        assert "Lineage" in lin or "lineage" in lin.lower() or h.id in lin


def test_cannot_spawn_rejected():
    with tempfile.TemporaryDirectory() as td:
        eng = HypothesisEngine(persist_path=Path(td) / "h.json")
        h = eng.reject_ungrounded("random idea")
        try:
            eng.spawn_candidate(h.id)
            assert False
        except RuntimeError:
            pass


if __name__ == "__main__":
    test_ungrounded_rejected()
    print("  ✓ ungrounded")
    test_generate_requires_evidence()
    print("  ✓ evidence")
    test_spawn_does_not_change_parent_fingerprint()
    print("  ✓ spawn new")
    test_pipeline_no_shortcut()
    print("  ✓ pipeline")
    test_cannot_spawn_rejected()
    print("  ✓ no spawn rejected")
    print("All quant v0.7 hypothesis tests passed.")
