"""Learning engine regression tests (v2.10)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.learning import LearningEngine
from agents import PersonalAgent


def make_orch(td: Path):
    orch = Orchestrator(memory=Memory(session_id="l1", persist_dir=td), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.learning = LearningEngine(orch, persist_dir=td / "learning")
    return orch


def test_observe_and_planner_recs():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        le = orch.learning
        for _ in range(5):
            le.observe_route("desktop", ok=False, latency_ms=100)
        for _ in range(5):
            le.observe_route("personal", ok=True, latency_ms=50)
        recs = le.analyze()
        titles = " ".join(r.title for r in le.recommendations)
        assert "desktop" in titles.lower() or any(r.category == "planner" for r in le.recommendations)
        bias = le.planner_agent_bias()
        assert bias.get("personal", 0) > bias.get("desktop", -1)


def test_collab_policy_learning():
    with tempfile.TemporaryDirectory() as tmp:
        le = make_orch(Path(tmp)).learning
        for _ in range(4):
            le.observe_collab("consensus", ok=True, disagreement=0.2)
        for _ in range(4):
            le.observe_collab("parallel", ok=True, disagreement=0.8)
        le.analyze()
        assert le.suggested_collab_mode() in ("consensus", "parallel", None)
        assert any(r.category == "collaboration" for r in le.recommendations) or le.suggested_collab_mode()


def test_workflow_and_retrieval():
    with tempfile.TemporaryDirectory() as tmp:
        le = make_orch(Path(tmp)).learning
        for _ in range(4):
            le.observe_workflow_step("import", ok=False, latency_ms=4000)
        le.observe_retrieval_feedback("invoice kubernetes", useful=False)
        le.observe_retrieval_feedback("invoice OCR", useful=False)
        le.observe_retrieval_feedback("kubernetes deploy", useful=True)
        le.observe_retrieval_feedback("kubernetes deploy", useful=True)
        le.observe_retrieval_feedback("kubernetes deploy", useful=True)
        le.analyze()
        assert any(r.category in ("workflow", "retrieval") for r in le.recommendations)
        assert le.retrieval_term_boost("kubernetes") >= le.retrieval_term_boost("xyzabc")


def test_apply_rollback_safety():
    with tempfile.TemporaryDirectory() as tmp:
        le = make_orch(Path(tmp)).learning
        for _ in range(4):
            le.observe_route("slowpoke", ok=False, latency_ms=50)
        le.analyze()
        assert le.recommendations
        rid = le.recommendations[0].id
        assert le.apply_recommendation(rid)["ok"]
        assert le.recommendations[0].applied
        assert le.rollback_recommendation(rid)["ok"]
        assert not le.recommendations[0].applied


def test_evaluation_ingest():
    with tempfile.TemporaryDirectory() as tmp:
        le = make_orch(Path(tmp)).learning
        le.ingest_evaluation({
            "id": "eval_test",
            "metrics": {"success_rate": 0.5},
            "suites": {"planner": {"success_rate": 0.5}},
        })
        assert any(h.get("type") == "evaluation" for h in le.history)
        assert any("planner" in r.title for r in le.recommendations)


def test_persistence_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        le = make_orch(td).learning
        le.observe_route("personal", ok=True, latency_ms=10)
        le.observe_route("personal", ok=True, latency_ms=10)
        le.observe_route("personal", ok=True, latency_ms=10)
        le.analyze()
        le._save()
        le2 = LearningEngine(make_orch(td), persist_dir=td / "learning")
        assert le2.routing_stats["personal"]["n"] >= 3


def test_report_and_status():
    with tempfile.TemporaryDirectory() as tmp:
        le = make_orch(Path(tmp)).learning
        le.observe_route("personal", ok=True, latency_ms=10)
        le.analyze()
        text = le.report()
        assert "Learning report" in text
        st = le.status()
        assert "recommendations" in st


if __name__ == "__main__":
    test_observe_and_planner_recs()
    print("  ✓ planner learning")
    test_collab_policy_learning()
    print("  ✓ collab policy")
    test_workflow_and_retrieval()
    print("  ✓ workflow/retrieval")
    test_apply_rollback_safety()
    print("  ✓ apply/rollback")
    test_evaluation_ingest()
    print("  ✓ evaluation")
    test_persistence_deterministic()
    print("  ✓ persistence")
    test_report_and_status()
    print("  ✓ report")
    print("All v2.10 learning tests passed.")
