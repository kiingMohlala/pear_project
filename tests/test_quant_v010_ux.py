"""Operator UX tests (Quant v0.10)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quant.operator_ux import QuantOperatorUX, _fmt_decision_explanation
from quant.research_lab import ResearchLab
from quant.hypothesis_engine import HypothesisEngine
from quant.research_review import ResearchReviewBoard
from quant.dsl import parse_strategy
from quant.data import synthetic_ohlcv
from core.connectors.quant_connector import QuantConnector


def _seed(td: Path):
    lab = ResearchLab(memory_path=td / "mem.json")
    series = synthetic_ohlcv(n=140, seed=1)
    ids = []
    for f, s in [(5, 20), (8, 30), (4, 15)]:
        exp = lab.run_experiment(parse_strategy({"name": "sma_cross", "params": {"fast": f, "slow": s}}), series)
        ids.append(exp.id)
    hyp = HypothesisEngine(memory=lab.memory, persist_path=td / "h.json")
    hyp.generate_from_memory(family="sma")
    board = ResearchReviewBoard(memory=lab.memory, persist_path=td / "b.json")
    return lab, hyp, board, ids


def test_dashboard():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        lab, hyp, board, ids = _seed(td)
        ux = QuantOperatorUX(memory=lab.memory, hyp_engine=hyp, board=board, data_dir=td)
        d = ux.dashboard()
        assert d["health"]["zero_real_orders"] is True
        assert d["health"]["memory_experiments"] >= 3
        text = ux.dashboard_text()
        assert "DASHBOARD" in text
        assert "DISABLED" in text


def test_candidate_and_hypothesis_views():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        lab, hyp, board, ids = _seed(td)
        ux = QuantOperatorUX(memory=lab.memory, hyp_engine=hyp, board=board, data_dir=td)
        cv = ux.candidate_view(ids[0])
        assert cv["candidate"]["experiment_id"] == ids[0]
        assert "failure" in str(cv.get("known_failure_modes")).lower() or isinstance(cv.get("known_failure_modes"), list)
        text = ux.candidate_view_text(ids[0])
        assert ids[0] in text
        queue = ux.hypotheses_queue()
        assert isinstance(queue, list)
        if hyp.hypotheses:
            hid = next(iter(hyp.hypotheses))
            hv = ux.hypothesis_view(hid)
            assert hv["id"] == hid
            assert "falsification" in hv
            assert "HYPOTHESIS" in hv["explanation"] or "Observed" in hv["explanation"]


def test_decision_explanation_format():
    text = _fmt_decision_explanation({
        "decision": "CONTINUE_OBSERVATION",
        "rationale": ["Evidence is promising but incomplete"],
        "scorecard": {
            "evidence_count": 7,
            "markets_tested": 7,
            "timeframes_tested": 3,
            "oos_periods": 11,
            "shadow_duration_days": 62,
            "trade_count": 40,
            "confidence": "moderate",
            "max_drawdown": 0.1,
            "parameter_stability": 0.7,
            "oos_sharpe": 0.4,
            "regime_robustness": 0.3,
            "failure_modes": ["weak ranging-market performance"],
            "backtest_paper_divergence": "MEDIUM",
        },
    })
    assert "DECISION: CONTINUE_OBSERVATION" in text
    assert "Evidence" in text
    assert "Strengths" in text
    assert "Weaknesses" in text
    assert "Reason" in text


def test_lineage_navigable():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        lab, hyp, board, ids = _seed(td)
        ux = QuantOperatorUX(memory=lab.memory, hyp_engine=hyp, board=board, data_dir=td)
        if hyp.hypotheses:
            hid = next(iter(hyp.hypotheses))
            lin = ux.lineage_view(hypothesis_id=hid)
            assert "nodes" in lin or "lineage" in lin
            assert ux.lineage_text(hypothesis_id=hid)


def test_connector_dashboard_no_trading():
    with tempfile.TemporaryDirectory() as td:
        q = QuantConnector(data_dir=Path(td))
        q.connect()
        q.execute("quant_research", fast=5, slow=20, n_bars=100, seed=1)
        r = q.execute("quant_dashboard")
        assert r.ok
        assert "DISABLED" in r.data.get("text", "") or r.data["health"]["zero_real_orders"] is True
        # still forbid orders
        bad = q.execute("place_order")
        assert not bad.ok


if __name__ == "__main__":
    test_dashboard()
    print("  ✓ dashboard")
    test_candidate_and_hypothesis_views()
    print("  ✓ views")
    test_decision_explanation_format()
    print("  ✓ decision text")
    test_lineage_navigable()
    print("  ✓ lineage")
    test_connector_dashboard_no_trading()
    print("  ✓ connector ux")
    print("All quant v0.10 UX tests passed.")
