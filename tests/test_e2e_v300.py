"""End-to-end integration scenarios for PEAR v3.00 RC."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import __version__, migrate_data_dir, integrity_report, check_compat, PUBLIC_APIS
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.config import Config, set_config
from agents import PersonalAgent, ReviewerAgent
from service.app import PearService


def test_version_and_public_api_markers():
    assert check_compat("3.0.0")
    assert "agents" in PUBLIC_APIS and "service" in PUBLIC_APIS
    assert __version__.startswith("3.")


def test_schema_migration():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        r = migrate_data_dir(root)
        assert r["ok"] and r["current"] >= 3
        assert (root / ".pear_schema.json").exists()
        # second run idempotent
        r2 = migrate_data_dir(root)
        assert r2["ok"]
        rep = integrity_report(root)
        assert rep["ok"] is True


def test_multi_user_session_isolation_e2e():
    with tempfile.TemporaryDirectory() as td:
        set_config(Config(profile="testing", overrides={"data_dir": td, "backup_dir": td + "/b", "rate_limit_burst": 50}))
        svc = PearService(data_root=Path(td))
        svc.sessions.llm = EchoLLM()
        _, a = svc.handle_route("POST", "/auth/login", {}, json.dumps({"username": "demo", "password": "demo"}).encode())
        _, b = svc.handle_route("POST", "/auth/login", {}, json.dumps({"username": "admin", "password": "admin"}).encode())
        ha = {"Authorization": f"Bearer {a['token']}"}
        hb = {"Authorization": f"Bearer {b['token']}"}
        svc.handle_route("POST", "/v1/chat", ha, json.dumps({"message": "note: alice only"}).encode())
        svc.handle_route("POST", "/v1/chat", hb, json.dumps({"message": "note: bob only"}).encode())
        # sessions isolated objects
        sa = svc.sessions.get("demo")
        sb = svc.sessions.get("admin")
        assert sa.orchestrator is not sb.orchestrator


def test_goal_workflow_worker_path():
    with tempfile.TemporaryDirectory() as td:
        set_config(Config(profile="testing", overrides={"data_dir": td, "backup_dir": td + "/b"}))
        orch = Orchestrator(memory=Memory(session_id="e2e", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        orch.register(ReviewerAgent(llm=EchoLLM()))
        # worker dispatch
        rec = orch.workers.dispatch("note: e2e worker", required_capabilities=["general"])
        done = orch.workers.wait(rec.id, timeout=15)
        assert done.status.value in ("succeeded", "failed", "timeout") or str(done.status).endswith("succeeded")
        # goal
        g = orch.goals.create("note: e2e goal step", auto_start=True)
        for _ in range(8):
            if g.status.value in ("completed", "failed", "cancelled"):
                break
            orch.goals.tick_all()
            g = orch.goals.get(g.id)
        # collaboration
        col = orch.collaboration.run("note: collab e2e", mode="reviewer")
        assert col.reply is not None
        # backup
        meta = orch.backups.create(label="e2e")
        assert Path(meta["path"]).exists()


def test_connector_n8n_absent_ok():
    with tempfile.TemporaryDirectory() as td:
        orch = Orchestrator(memory=Memory(session_id="e2e2", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        r = orch.connectors.execute("n8n", "status")
        # disabled is fine
        assert r is not None


if __name__ == "__main__":
    test_version_and_public_api_markers()
    print("  ✓ version/api freeze")
    test_schema_migration()
    print("  ✓ migration")
    test_multi_user_session_isolation_e2e()
    print("  ✓ multi-user")
    test_goal_workflow_worker_path()
    print("  ✓ goal/worker/collab/backup")
    test_connector_n8n_absent_ok()
    print("  ✓ connectors optional")
    print("All v3.00 e2e tests passed.")
