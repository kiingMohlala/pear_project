"""Desktop agent & sandbox regression tests (v0.70)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.desktop import Workspace, list_directory, copy_file, delete_file, create_folder
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.tool_registry import build_default_registry
from agents import DesktopAgent, PersonalAgent


def test_registry_has_desktop_tools():
    reg = build_default_registry()
    for name in (
        "list_directory", "copy_file", "move_file", "rename_file",
        "delete_file", "create_folder", "get_system_info", "take_screenshot",
    ):
        assert reg.has(name), name


def test_workspace_sandbox():
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        inside = Path(td) / "a.txt"
        inside.write_text("hi")
        assert ws.is_inside(inside)
        outside = Path("/tmp/pear_outside_test_should_fail")
        assert not ws.is_inside(outside)
        try:
            ws.require_inside(outside)
            assert False, "should raise"
        except PermissionError:
            pass


def test_file_ops_in_workspace():
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        create_folder(Path(td) / "sub", workspace=ws)
        src = Path(td) / "sub" / "f.txt"
        src.write_text("data")
        dest = Path(td) / "sub" / "f2.txt"
        r = copy_file(src, dest, workspace=ws)
        assert r["ok"]
        r = delete_file(dest, workspace=ws, use_trash=True)
        assert r["ok"]
        assert "trash" in r


def test_agent_list_and_mkdir():
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        agent = DesktopAgent(workspace=ws)
        agent.memory = Memory(session_id="d1")
        r = agent.think("create folder notes")
        assert r["ok"]
        r = agent.think("list dir .")
        assert r["ok"]
        assert "notes" in r["reply"]


def test_delete_requires_approval():
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        f = Path(td) / "x.txt"
        f.write_text("x")
        agent = DesktopAgent(workspace=ws)
        agent.memory = Memory(session_id="d2")
        r = agent.think(f"delete file {f}")
        assert r.get("action") == "needs_approval"
        aid = r["approval_id"]
        r2 = agent.think(f"approve desktop {aid}")
        assert r2["ok"]
        assert not f.exists()


def test_outside_workspace_blocked():
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        agent = DesktopAgent(workspace=ws)
        agent.memory = Memory(session_id="d3")
        r = agent.think("list dir /etc")
        assert r.get("ok") is False or "outside" in (r.get("reply") or "").lower() or "error" in (r.get("reply") or "").lower()


def test_planner_routes_desktop():
    orch = Orchestrator(memory=Memory(session_id="d4"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    with tempfile.TemporaryDirectory() as td:
        orch.register(DesktopAgent(workspace=Workspace(roots=[Path(td)])))
        task = orch.plan("list directory and search files in workspace")
        assert task.assigned_agent == "desktop"


def test_workflow_integration():
    from core.workflow import Workflow, WorkflowStep, StepType, WorkflowStatus
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        mem = Memory(session_id="d5", persist_dir=td_path)
        orch = Orchestrator(memory=mem, llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        orch.register(DesktopAgent(workspace=Workspace(roots=[td_path])))
        wf = Workflow(
            name="desk_demo",
            steps=[
                WorkflowStep(
                    name="mkdir",
                    type=StepType.TASK,
                    objective=f"create folder {td_path / 'wf_out'}",
                ),
                WorkflowStep(
                    name="list",
                    type=StepType.TASK,
                    objective=f"list dir {td_path}",
                ),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("desk_demo")
        assert run.status == WorkflowStatus.COMPLETED


def test_permissions_groups():
    agent = DesktopAgent(workspace=Workspace(roots=[Path(".")]))
    assert agent.permissions.can("list_directory")
    assert agent.permissions.can("delete_file")
    agent.permissions.set_policy("delete_file", "never")
    assert not agent.permissions.can("delete_file")


if __name__ == "__main__":
    test_registry_has_desktop_tools()
    print("  ✓ registry")
    test_workspace_sandbox()
    print("  ✓ sandbox")
    test_file_ops_in_workspace()
    print("  ✓ file ops")
    test_agent_list_and_mkdir()
    print("  ✓ list/mkdir")
    test_delete_requires_approval()
    print("  ✓ delete approval")
    test_outside_workspace_blocked()
    print("  ✓ outside blocked")
    test_planner_routes_desktop()
    print("  ✓ planner")
    test_workflow_integration()
    print("  ✓ workflow")
    test_permissions_groups()
    print("  ✓ permissions")
    print("All v0.70 desktop tests passed.")
