"""Connector framework regression tests (v0.90)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.connectors import (
    build_default_connectors,
    CredentialStore,
    ConnectorStatus,
)
from core.connectors.github_connector import GitHubConnector
from core.connectors.email_connector import EmailConnector
from core.desktop import Workspace
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.workflow import Workflow, WorkflowStep, StepType, WorkflowStatus
from agents import PersonalAgent


def test_registry_lists_reference_connectors():
    reg = build_default_connectors()
    names = {c["name"] for c in reg.list()}
    assert {"local_files", "email", "calendar", "github"} <= names


def test_local_files_connect_and_list():
    with tempfile.TemporaryDirectory() as td:
        ws = Workspace(roots=[Path(td)])
        reg = build_default_connectors(workspace=ws)
        r = reg.connect("local_files")
        assert r.ok
        (Path(td) / "a.txt").write_text("hi")
        out = reg.execute("local_files", "list", path=td)
        assert out.ok
        names = [e["name"] for e in (out.data or {}).get("entries", [])]
        assert "a.txt" in names


def test_credential_persistence():
    with tempfile.TemporaryDirectory() as td:
        store = CredentialStore(path=Path(td) / "creds.enc")
        store.set("github", {"token": "dry-run"})
        store2 = CredentialStore(path=Path(td) / "creds.enc")
        assert store2.get("github")["token"] == "dry-run"
        assert "github" in store2.list_connectors()


def test_github_dry_run_and_approval():
    gh = GitHubConnector()
    r = gh.connect({"token": "dry-run"})
    assert r.ok
    assert gh.authenticate({"token": "dry-run"}).ok
    gh.status = ConnectorStatus.CONNECTED
    listed = gh.execute("list_repos")
    assert listed.ok and (listed.data or {}).get("dry_run")
    create = gh.execute("create_issue", repo="o/r", title="t")
    assert create.needs_approval
    create2 = gh.execute("create_issue", repo="o/r", title="t", approved=True)
    assert create2.ok


def test_email_send_approval_and_dry_run():
    em = EmailConnector()
    em.connect({"username": "a@b.com", "password": "x"})
    em.authenticate({"username": "a@b.com", "password": "x"})
    em.status = ConnectorStatus.CONNECTED
    r = em.execute("send", to="c@d.com", subject="Hi", body="Hello")
    assert r.needs_approval
    r2 = em.execute("send", to="c@d.com", subject="Hi", body="Hello", approved=True)
    assert r2.ok and (r2.data or {}).get("dry_run")


def test_calendar_crud():
    with tempfile.TemporaryDirectory() as td:
        from core.connectors.calendar_connector import CalendarConnector
        cal = CalendarConnector(store_path=Path(td) / "cal.json")
        assert cal.connect().ok
        cal.status = ConnectorStatus.CONNECTED
        r = cal.execute("create_event", title="Standup", start="2026-08-02T09:00")
        assert r.ok
        listed = cal.execute("list_events")
        assert len(listed.data["events"]) == 1


def test_registry_retries_on_failure():
    reg = build_default_connectors()
    # unknown action fails after retries without crashing
    reg.connect("calendar")
    r = reg.execute("calendar", "nope_action", retries=1)
    assert not r.ok


def test_orchestrator_connectors():
    orch = Orchestrator(memory=Memory(session_id="c1"), llm=EchoLLM())
    assert orch.connectors.has("github")
    r = orch.connectors.connect("calendar")
    assert r.ok


def test_workflow_connector_step():
    with tempfile.TemporaryDirectory() as td:
        mem = Memory(session_id="c2", persist_dir=Path(td))
        orch = Orchestrator(memory=mem, llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        orch.connectors.connect("calendar")
        wf = Workflow(
            name="cal_demo",
            steps=[
                WorkflowStep(
                    name="create",
                    type=StepType.CONNECTOR,
                    connector="calendar",
                    connector_action="create_event",
                    connector_params={"title": "WF Event"},
                    save_as="evt",
                ),
                WorkflowStep(
                    name="list",
                    type=StepType.CONNECTOR,
                    connector="calendar",
                    connector_action="list_events",
                    save_as="events",
                ),
            ],
        )
        orch.workflows.register(wf)
        run = orch.workflows.start("cal_demo")
        assert run.status == WorkflowStatus.COMPLETED
        assert run.context.get("evt", {}).get("ok")


if __name__ == "__main__":
    test_registry_lists_reference_connectors()
    print("  ✓ registry")
    test_local_files_connect_and_list()
    print("  ✓ local_files")
    test_credential_persistence()
    print("  ✓ credentials")
    test_github_dry_run_and_approval()
    print("  ✓ github")
    test_email_send_approval_and_dry_run()
    print("  ✓ email")
    test_calendar_crud()
    print("  ✓ calendar")
    test_registry_retries_on_failure()
    print("  ✓ retries")
    test_orchestrator_connectors()
    print("  ✓ orchestrator")
    test_workflow_connector_step()
    print("  ✓ workflow")
    print("All v0.90 connector tests passed.")
