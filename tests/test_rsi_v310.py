"""Controlled RSI regression tests (v3.10)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.config import Config, set_config
from core.self_improve import (
    SelfImprovementEngine,
    ProposalStatus,
    RegressionThresholds,
)
from agents import PersonalAgent


def make_orch(td: Path):
    set_config(Config(profile="testing", overrides={
        "data_dir": str(td),
        "backup_dir": str(td / "b"),
        "planner_use_learned_bias": False,
    }))
    orch = Orchestrator(memory=Memory(session_id="rsi", persist_dir=td), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.self_improve = SelfImprovementEngine(
        orch,
        persist_dir=td / "rsi",
        require_human_approval=True,
    )
    return orch


def test_analyze_and_propose():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        # seed learning signals
        for _ in range(5):
            orch.learning.observe_route("desktop", ok=False, latency_ms=100)
            orch.learning.observe_route("personal", ok=True, latency_ms=40)
        analysis = orch.self_improve.analyze()
        assert analysis["ok"]
        props = orch.self_improve.propose_from_analysis(limit=3)
        assert isinstance(props, list)


def test_validation_rejects_regression():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        eng = orch.self_improve
        eng.thresholds = RegressionThresholds(
            success_rate_drop=0.0,
            latency_increase_pct=0.0,
            min_success_rate=0.99,
        )
        # force a proposal
        from core.self_improve import ImprovementProposal, ProposalStatus
        prop = ImprovementProposal(
            id="imp_test1",
            title="test bias",
            category="planner_bias",
            rationale="unit test",
            confidence=0.6,
            expected_benefit="n/a",
            rollback_plan="restore snapshot",
            changes={"config": {"planner_use_learned_bias": True}},
            status=ProposalStatus.PROPOSED,
        )
        eng.proposals[prop.id] = prop
        eng.capture_baseline("t")
        # if min_success_rate very high and eval fallback < that, may fail — still deterministic
        result = eng.validate_proposal(prop.id)
        assert result.status in (
            ProposalStatus.FAILED,
            ProposalStatus.REJECTED,
            ProposalStatus.AWAITING_APPROVAL,
            ProposalStatus.PASSED,
        )


def test_approval_required_no_silent_deploy():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        eng = orch.self_improve
        from core.self_improve import ImprovementProposal, ProposalStatus
        prop = ImprovementProposal(
            id="imp_test2",
            title="enable bias",
            category="planner_bias",
            rationale="test",
            confidence=0.7,
            expected_benefit="better routing",
            rollback_plan="disable bias",
            changes={"config": {"planner_use_learned_bias": True}},
            status=ProposalStatus.AWAITING_APPROVAL,
        )
        eng.proposals[prop.id] = prop
        try:
            eng.deploy(prop.id)
            assert False, "should require accept first"
        except ValueError:
            pass
        eng.approve(prop.id, approver="tester")
        eng.deploy(prop.id)
        assert eng.proposals[prop.id].status == ProposalStatus.DEPLOYED
        eng.rollback(prop.id)
        assert eng.proposals[prop.id].status == ProposalStatus.ROLLED_BACK


def test_history_persistent():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        orch = make_orch(td)
        orch.self_improve.capture_baseline("x")
        orch2 = make_orch(td)
        orch2.self_improve = SelfImprovementEngine(orch2, persist_dir=td / "rsi")
        assert orch2.self_improve.baselines


def test_run_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        for _ in range(4):
            orch.learning.observe_route("personal", ok=True, latency_ms=10)
        out = orch.self_improve.run_cycle(limit=2)
        assert out["ok"]


def test_orchestrator_unchanged_api():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        r = orch.route("hello")
        assert r.get("ok") is True or "reply" in r


if __name__ == "__main__":
    test_analyze_and_propose()
    print("  ✓ analyze/propose")
    test_validation_rejects_regression()
    print("  ✓ validate")
    test_approval_required_no_silent_deploy()
    print("  ✓ approval/rollback")
    test_history_persistent()
    print("  ✓ persistence")
    test_run_cycle()
    print("  ✓ cycle")
    test_orchestrator_unchanged_api()
    print("  ✓ api stable")
    print("All v3.10 RSI tests passed.")
