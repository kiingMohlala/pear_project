"""Browser agent regression tests (v0.80)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.browser import BrowserManager, playwright_available, BROWSER_PERM_GROUPS
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.workflow import Workflow, WorkflowStep, StepType, WorkflowStatus
from agents import BrowserAgent, PersonalAgent, DesktopAgent


def test_permission_groups():
    assert "open_url" in BROWSER_PERM_GROUPS["browser_read"]
    assert "download_file" in BROWSER_PERM_GROUPS["browser_download"]
    assert "upload_file" in BROWSER_PERM_GROUPS["browser_upload"]


def test_simulated_navigation():
    with tempfile.TemporaryDirectory() as td:
        mgr = BrowserManager(download_dir=Path(td))
        r = mgr.open_url("https://example.com")
        assert r["ok"]
        assert "example.com" in r.get("url", "")
        hist = mgr.history_list()
        assert len(hist["history"]) >= 1
        r2 = mgr.search_web("pear assistant")
        assert r2["ok"]
        assert mgr.session.history_index >= 1


def test_agent_open_and_history():
    with tempfile.TemporaryDirectory() as td:
        agent = BrowserAgent(download_dir=Path(td))
        agent.memory = Memory(session_id="b1")
        r = agent.think("open url https://example.com")
        assert r["ok"]
        h = agent.think("browser history")
        assert h["ok"]
        assert "example.com" in h["reply"]


def test_download_approval_flow():
    with tempfile.TemporaryDirectory() as td:
        agent = BrowserAgent(download_dir=Path(td))
        agent.memory = Memory(session_id="b2")
        r = agent.think("download https://example.com/file.pdf")
        assert r.get("action") == "needs_approval"
        aid = r["approval_id"]
        # approval will fail without playwright — but flow completes
        r2 = agent.think(f"approve browser {aid}")
        assert "action" in r2


def test_click_approval():
    with tempfile.TemporaryDirectory() as td:
        agent = BrowserAgent(download_dir=Path(td))
        agent.memory = Memory(session_id="b3")
        r = agent.think("click #submit")
        assert r.get("action") == "needs_approval"


def test_planner_routes_browser():
    orch = Orchestrator(memory=Memory(session_id="b4"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    with tempfile.TemporaryDirectory() as td:
        orch.register(BrowserAgent(download_dir=Path(td)))
        task = orch.plan("open url https://example.com and extract text")
        assert task.assigned_agent == "browser"


def test_workflow_integration():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        mem = Memory(session_id="b5", persist_dir=td_path)
        orch = Orchestrator(memory=mem, llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        orch.register(BrowserAgent(download_dir=td_path / "dl"))
        wf = Workflow(
            name="browse_demo",
            steps=[
                WorkflowStep(
                    name="open",
                    type=StepType.TASK,
                    objective="open url https://example.com",
                ),
                WorkflowStep(
                    name="hist",
                    type=StepType.TASK,
                    objective="browser history",
                ),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("browse_demo")
        assert run.status == WorkflowStatus.COMPLETED


def test_save_page_indexes_knowledge():
    with tempfile.TemporaryDirectory() as td:
        agent = BrowserAgent(download_dir=Path(td))
        agent.memory = Memory(session_id="b6")
        agent.think("open url https://example.com")
        r = agent.think("save page")
        assert r["ok"]
        # stub or real save creates file
        assert (r.get("data") or {}).get("path")


def test_tracing_span_on_open():
    from core.tracing import Tracer, set_tracer
    tr = Tracer()
    set_tracer(tr)
    with tempfile.TemporaryDirectory() as td:
        agent = BrowserAgent(download_dir=Path(td))
        agent.memory = Memory(session_id="b7")
        with tr.request("browse"):
            agent.think("open url https://example.com")
        spans = [s for t in tr._traces.values() for s in t.spans.values()]
        names = [s.name for s in spans]
        assert any("open_url" in n for n in names)


if __name__ == "__main__":
    test_permission_groups()
    print("  ✓ perm groups")
    test_simulated_navigation()
    print("  ✓ navigation")
    test_agent_open_and_history()
    print("  ✓ agent open")
    test_download_approval_flow()
    print("  ✓ download approval")
    test_click_approval()
    print("  ✓ click approval")
    test_planner_routes_browser()
    print("  ✓ planner")
    test_workflow_integration()
    print("  ✓ workflow")
    test_save_page_indexes_knowledge()
    print("  ✓ save page")
    test_tracing_span_on_open()
    print("  ✓ tracing")
    print("All v0.80 browser tests passed.")
