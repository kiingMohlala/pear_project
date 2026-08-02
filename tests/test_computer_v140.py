"""Computer Use Agent regression tests (v1.40)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["PEAR_COMPUTER_BACKEND"] = "sim"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.computer import ComputerController, locate_elements_from_ocr
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import ComputerUseAgent, PersonalAgent
from evaluation.engine import EvaluationEngine


def test_controller_sim_actions():
    c = ComputerController()
    assert c.backend == "sim"
    assert c.click(10, 20)["ok"]
    assert c.type_text("hello")["ok"]
    assert c.scroll(-3)["ok"]
    assert c.drag(0, 0, 50, 50)["ok"]
    assert c.hotkey("ctrl", "s")["ok"]


def test_locate_elements():
    els = locate_elements_from_ocr("File\nEdit\nSave\nCancel", query="save")
    assert els
    assert "Save" in els[0].label


def test_agent_observe_click():
    agent = ComputerUseAgent(controller=ComputerController())
    agent.memory = Memory(session_id="cu1")
    obs = agent.think("capture ui")
    assert obs["ok"]
    assert obs.get("elements")
    r = agent.think("click Save")
    assert r["ok"]


def test_destructive_approval():
    agent = ComputerUseAgent(controller=ComputerController())
    agent.memory = Memory(session_id="cu2")
    agent.think("capture ui")
    # force a destructive label into elements
    from core.computer import UIElement
    agent.last_elements = [UIElement("1", "Empty Trash", 100, 100, 80, 20, 1.0)]
    r = agent.think("click Empty Trash")
    assert r.get("action") == "needs_approval"
    aid = r["approval_id"]
    r2 = agent.think(f"approve computer {aid}")
    assert r2["ok"]


def test_planner_routes():
    orch = Orchestrator(memory=Memory(session_id="cu3"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(ComputerUseAgent())
    task = orch.plan("click the Save button on screen")
    assert task.assigned_agent == "computer"


def test_eval_suite():
    eng = EvaluationEngine()
    report = eng.run(suites=["computer"], save_history=False, compare_baseline=False)
    assert report.suites["computer"].success_rate >= 0.75


if __name__ == "__main__":
    test_controller_sim_actions()
    print("  ✓ controller")
    test_locate_elements()
    print("  ✓ locate")
    test_agent_observe_click()
    print("  ✓ observe/click")
    test_destructive_approval()
    print("  ✓ approval")
    test_planner_routes()
    print("  ✓ planner")
    test_eval_suite()
    print("  ✓ eval")
    print("All v1.40 computer-use tests passed.")
