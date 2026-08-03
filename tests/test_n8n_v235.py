"""Optional n8n connector regression tests (v2.35) – fully offline."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.connectors.n8n_connector import N8NConnector
from core.connectors import build_default_connectors, N8NConnector as N8N
from core.connectors.base import ConnectorStatus
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent


class MockN8N:
    """Deterministic fake n8n HTTP API."""

    def __init__(self):
        self.calls = []
        self.workflows = [
            {"id": "wf_1", "name": "Invoice Sync"},
            {"id": "wf_2", "name": "Lead Notify"},
        ]
        self.executions: Dict[str, Dict[str, Any]] = {}

    def __call__(self, method: str, url: str, headers: dict, body: Optional[dict]):
        self.calls.append({"method": method, "url": url, "body": body})
        if method == "GET" and url.endswith("/api/v1/workflows"):
            return {"data": self.workflows}
        if method == "POST" and "/api/v1/workflows/" in url and url.endswith("/run"):
            eid = f"exec_{len(self.executions)+1}"
            self.executions[eid] = {"id": eid, "status": "running"}
            return {"id": eid, "status": "running"}
        if method == "POST" and "/webhook/" in url:
            eid = f"exec_wh_{len(self.executions)+1}"
            self.executions[eid] = {"id": eid, "status": "success"}
            return {"executionId": eid, "status": "success"}
        if method == "GET" and "/api/v1/executions/" in url:
            eid = url.rstrip("/").split("/")[-1]
            return self.executions.get(eid, {"id": eid, "status": "success", "finished": True})
        if method == "POST" and url.endswith("/stop"):
            return {"stopped": True}
        if method == "GET" and "/api/v1/executions" in url:
            return {"data": list(self.executions.values())}
        return {}


def test_disabled_by_default():
    c = N8NConnector()
    r = c.connect()
    assert r.ok is False
    r2 = c.execute("list_workflows")
    assert r2.ok is False


def test_list_and_execute_with_mock():
    mock = MockN8N()
    c = N8NConnector(base_url="http://n8n.test", api_key="k", http_client=mock)
    assert c.connect().ok
    assert c.authenticate({"api_key": "k"}).ok
    r = c.execute("list_workflows")
    assert r.ok and len(r.data["workflows"]) == 2
    r2 = c.execute("execute_workflow", workflow_id="wf_1", data={"x": 1})
    assert r2.ok and r2.data["execution_id"]
    eid = r2.data["execution_id"]
    r3 = c.execute("get_execution_status", execution_id=eid)
    assert r3.ok
    r4 = c.execute("cancel_execution", execution_id=eid)
    assert r4.ok and r4.data["status"] == "cancelled"
    r5 = c.execute("list_executions")
    assert r5.ok


def test_webhook_execute_path():
    mock = MockN8N()
    c = N8NConnector(base_url="http://n8n.test", http_client=mock)
    c.connect()
    r = c.execute("execute_workflow", workflow_id="webhook:demo", data={"a": 1})
    assert r.ok
    assert any("/webhook/" in call["url"] for call in mock.calls)


def test_callback_resumes_goal():
    with tempfile.TemporaryDirectory() as td:
        orch = Orchestrator(memory=Memory(session_id="n8n1", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        g = orch.goals.create("note: wait for n8n", auto_start=False)
        orch.goals.plan(g.id)
        orch.goals.start(g.id)
        orch.goals.wait(g.id, orch.goals.get(g.id).wait_reason.__class__("user_approval") if False else
                        __import__("core.goals", fromlist=["WaitReason"]).WaitReason.USER_APPROVAL,
                        note="await n8n")
        mock = MockN8N()
        c = N8NConnector(base_url="http://n8n.test", http_client=mock)
        c.orchestrator = orch
        c.connect()
        r = c.execute("handle_callback", payload={"goal_id": g.id, "execution_id": "exec_cb", "status": "success"})
        assert r.ok
        assert any(x.startswith("goal:") for x in r.data.get("resumed", []))
        st = orch.goals.get(g.id).status.value
        assert st in ("running", "completed", "failed")


def test_registry_includes_n8n_disabled():
    reg = build_default_connectors()
    assert reg.has("n8n") if hasattr(reg, "has") else "n8n" in [c["name"] for c in reg.list()]
    # execute without connect should fail soft
    r = reg.execute("n8n", "list_workflows")
    assert r.ok is False or (r.data and r.data.get("enabled") is False)


def test_pear_works_without_n8n():
    with tempfile.TemporaryDirectory() as td:
        orch = Orchestrator(memory=Memory(session_id="n8n2", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        r = orch.route("hello")
        assert r.get("ok") is True or "reply" in r


if __name__ == "__main__":
    test_disabled_by_default()
    print("  ✓ disabled by default")
    test_list_and_execute_with_mock()
    print("  ✓ list/execute/status/cancel")
    test_webhook_execute_path()
    print("  ✓ webhook path")
    test_callback_resumes_goal()
    print("  ✓ callback resume")
    test_registry_includes_n8n_disabled()
    print("  ✓ registry")
    test_pear_works_without_n8n()
    print("  ✓ no n8n dependency")
    print("All v2.35 n8n tests passed.")
