"""Independent review & ranking tests (Quant v0.8)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.data import synthetic_ohlcv, Series
from quant.dsl import parse_strategy
from quant.independent_review import IndependentValidator, assert_disjoint, series_fingerprint
from quant.scorecard import CandidateScorecard, rank_scorecards
from quant.research_decision import decide, ResearchDecisionType, compare_hypotheses
from quant.research_review import ResearchReviewBoard
from quant.research_lab import ResearchLab
from quant.hypothesis_engine import HypothesisEngine


def test_leakage_blocked():
    s = synthetic_ohlcv(n=50, seed=1)
    try:
        assert_disjoint(s, s)
        assert False
    except ValueError as e:
        assert "overlap" in str(e).lower() or "fingerprint" in str(e).lower()


def test_disjoint_ok():
    a = synthetic_ohlcv(n=40, seed=1)
    b = synthetic_ohlcv(n=40, seed=2)
    # force non-overlapping timestamps
    for i, bar in enumerate(b.bars):
        bar.ts = 10_000 + i
    assert_disjoint(a, b)


def test_independent_review_no_history():
    strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 20}})
    research = synthetic_ohlcv(n=80, seed=1)
    independent = synthetic_ohlcv(n=80, seed=3)
    for i, bar in enumerate(independent.bars):
        bar.ts = 50_000 + i
    v = IndependentValidator()
    r = v.review(strat, independent, research_series=research)
    assert r.leakage_checked
    assert "sharpe" in r.metrics


def test_scorecard_not_return_primary():
    high_ret = CandidateScorecard(
        "a", hypothesis_id="H1", oos_sharpe=0.1, max_drawdown=0.05, profit_factor=1.0,
        expectancy=0.01, trade_count=20, total_return=0.9, evidence_count=5, markets_tested=2, timeframes_tested=2,
    )
    high_quality = CandidateScorecard(
        "b", hypothesis_id="H2", oos_sharpe=1.0, max_drawdown=0.08, profit_factor=1.5,
        expectancy=0.02, trade_count=20, total_return=0.1, evidence_count=5, markets_tested=2, timeframes_tested=2,
    )
    ranked = rank_scorecards([high_ret, high_quality])
    assert ranked[0].candidate_id == "b"


def test_decisions():
    weak = CandidateScorecard("x", trade_count=1, evidence_count=0, oos_sharpe=0.0)
    d = decide(weak)
    assert d.decision == ResearchDecisionType.INSUFFICIENT_EVIDENCE

    bad_div = CandidateScorecard(
        "y", oos_sharpe=0.5, trade_count=20, evidence_count=5, markets_tested=2,
        backtest_paper_divergence="HIGH", max_drawdown=0.1,
    )
    d2 = decide(bad_div)
    assert d2.decision == ResearchDecisionType.RETIRE


def test_board_package_and_compare():
    with tempfile.TemporaryDirectory() as td:
        board = ResearchReviewBoard(persist_path=Path(td) / "board.json")
        strat = parse_strategy({"name": "sma_cross", "params": {"fast": 5, "slow": 18}})
        research = synthetic_ohlcv(n=60, seed=1)
        independent = synthetic_ohlcv(n=60, seed=5)
        for i, bar in enumerate(independent.bars):
            bar.ts = 80_000 + i
        pkg = board.full_review_package(
            strat, independent, research,
            hypothesis_id="H-TEST",
            evidence_count=4,
            markets_tested=2,
            timeframes_tested=2,
        )
        assert "decision" in pkg
        assert "scorecard" in pkg
        assert "No real-money" in pkg["note"]

        # second candidate for comparison
        strat2 = parse_strategy({"name": "sma_cross", "params": {"fast": 8, "slow": 25}})
        ind2 = synthetic_ohlcv(n=60, seed=6)
        for i, bar in enumerate(ind2.bars):
            bar.ts = 90_000 + i
        board.independent_evaluate(
            strat2, ind2, research_series=research,
            hypothesis_id="H-TEST2", evidence_count=4, markets_tested=2, timeframes_tested=2,
        )
        cmp = board.compare()
        assert cmp["criterion"].startswith("composite")
        assert "winner_hypothesis" in cmp


def test_lineage_query():
    with tempfile.TemporaryDirectory() as td:
        lab = ResearchLab(memory_path=Path(td) / "mem.json")
        series = synthetic_ohlcv(n=140, seed=2)
        for f, s in [(5, 20), (8, 30)]:
            lab.run_experiment(parse_strategy({"name": "sma_cross", "params": {"fast": f, "slow": s}}), series)
        eng = HypothesisEngine(memory=lab.memory, persist_path=Path(td) / "h.json")
        hyps = eng.generate_from_memory(family="sma")
        assert hyps
        board = ResearchReviewBoard(memory=lab.memory, persist_path=Path(td) / "b.json")
        lin = board.lineage_query(hypothesis_id=hyps[0].id, hypothesis_engine=eng)
        assert lin.get("nodes") or lin.get("lineage") is not None


if __name__ == "__main__":
    test_leakage_blocked()
    print("  ✓ leakage block")
    test_disjoint_ok()
    print("  ✓ disjoint")
    test_independent_review_no_history()
    print("  ✓ independent")
    test_scorecard_not_return_primary()
    print("  ✓ rank")
    test_decisions()
    print("  ✓ decisions")
    test_board_package_and_compare()
    print("  ✓ board")
    test_lineage_query()
    print("  ✓ lineage")
    print("All quant v0.8 independent tests passed.")
