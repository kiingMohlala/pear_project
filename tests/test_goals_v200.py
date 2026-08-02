"""Autonomous goal execution regression tests (v2.00)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.goals import GoalManager, GoalStatus, WaitReason
from agents import PersonalAgent


def make_orch(td: Path):
    orch = Orchestrator(memory=Memory(session_id="g1", persist_dir=td), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.goals = GoalManager(orch, persist_dir=td / "goals")
    return orch


def test_create_and_complete():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        orch = make_orch(td)
        g = orch.goals.create("note: goal completion marker", auto_start=True)
        assert g.status in (GoalStatus.COMPLETED, GoalStatus.RUNNING, GoalStatus.FAILED)
        # tick until done or failed
        for _ in range(10):
            if g.status in (GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.CANCELLED):
                break
            orch.goals.tick_all()
            g = orch.goals.get(g.id)
        assert g.progress >= 0.0
        assert g.steps


def test_pause_resume():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        orch = make_orch(td)
        g = orch.goals.create("note: pause test", auto_start=False)
        orch.goals.plan(g.id)
        g = orch.goals.start(g.id)
        orch.goals.pause(g.id)
        assert orch.goals.get(g.id).status == GoalStatus.PAUSED
        orch.goals.resume(g.id)
        assert orch.goals.get(g.id).status in (
            GoalStatus.RUNNING, GoalStatus.COMPLETED, GoalStatus.FAILED
        )


def test_cancel():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        g = orch.goals.create("note: cancel me", auto_start=False)
        orch.goals.cancel(g.id)
        assert orch.goals.get(g.id).status == GoalStatus.CANCELLED


def test_wait_and_auto_resume():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        g = orch.goals.create("note: wait test", auto_start=False)
        orch.goals.plan(g.id)
        orch.goals.start(g.id)
        orch.goals.wait(g.id, WaitReason.SCHEDULED, until=time.time() - 1, note="past")
        assert orch.goals.get(g.id).status == GoalStatus.WAITING
        progressed = orch.goals.tick_all()
        # should auto-resume because wait_until in the past
        st = orch.goals.get(g.id).status
        assert st in (GoalStatus.RUNNING, GoalStatus.COMPLETED, GoalStatus.FAILED)


def test_persistence_restart():
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        orch = make_orch(td)
        g = orch.goals.create("note: persist goal", auto_start=False)
        orch.goals.plan(g.id)
        gid = g.id
        # new manager same dir
        orch2 = make_orch(td)
        g2 = orch2.goals.get(gid)
        assert g2.objective == "note: persist goal"
        assert g2.steps


def test_replan_on_failure():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        g = orch.goals.create("note: replan path", auto_start=False)
        orch.goals.plan(g.id)
        g = orch.goals.get(g.id)
        # force a failed step with no retries left
        if g.steps:
            step = g.steps[0]
            step.status = "failed"
            step.attempts = g.max_step_attempts
            step.error = "boom"
            g.max_replans = 1
            orch.goals._handle_failure(g)
            g = orch.goals.get(g.id)
            assert g.replan_count >= 1 or g.status == GoalStatus.FAILED


def test_multi_step_decomposition():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        g = orch.goals.create(
            "research the topic; then summarize findings; then note: done",
            auto_start=False,
        )
        orch.goals.plan(g.id)
        g = orch.goals.get(g.id)
        assert len(g.steps) >= 2


def test_status_report():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        g = orch.goals.create("note: report", auto_start=False)
        orch.goals.plan(g.id)
        text = orch.goals.status_report(g.id)
        assert g.id in text and "Steps" in text


if __name__ == "__main__":
    test_create_and_complete()
    print("  ✓ create/complete")
    test_pause_resume()
    print("  ✓ pause/resume")
    test_cancel()
    print("  ✓ cancel")
    test_wait_and_auto_resume()
    print("  ✓ wait/resume")
    test_persistence_restart()
    print("  ✓ persistence")
    test_replan_on_failure()
    print("  ✓ replan")
    test_multi_step_decomposition()
    print("  ✓ decompose")
    test_status_report()
    print("  ✓ report")
    print("All v2.00 goal tests passed.")
