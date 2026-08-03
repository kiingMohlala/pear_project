"""Operations & production hardening regression tests (v2.40)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import Config, ConfigError, set_config
from core.logging_util import setup_logging, new_correlation_id, get_correlation_id, bind_context
from core.audit import AuditLog
from core.ratelimit import RateLimiter
from core.backup import BackupManager
from core.ops import integrity_check, resource_usage, diagnostics
from core.workers import WorkerManager
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent


def test_config_profiles():
    c = Config(profile="development")
    assert c["profile"] == "development"
    c2 = Config(profile="production")
    assert c2["log_json"] is True
    try:
        Config(profile="nope")
        assert False
    except ConfigError:
        pass


def test_config_validation_and_reload():
    with tempfile.TemporaryDirectory() as td:
        c = Config(profile="testing", overrides={"data_dir": td, "backup_dir": td + "/b"})
        path = c.save(Path(td) / "config.json")
        c.update(rate_limit_per_minute=42)
        c.save(path)
        c2 = Config(profile="testing")
        c2.load_file(path)
        assert c2["rate_limit_per_minute"] == 42
        # hot reload
        data = json.loads(path.read_text())
        data["rate_limit_per_minute"] = 99
        path.write_text(json.dumps(data))
        # force mtime change awareness
        c2._mtime = 0
        assert c2.reload() is True
        assert c2["rate_limit_per_minute"] == 99


def test_structured_logging_correlation():
    setup_logging("INFO", json_mode=False)
    cid = new_correlation_id()
    assert get_correlation_id() == cid
    bind_context(goal_id="g1")
    assert get_correlation_id()


def test_rate_limiter():
    rl = RateLimiter(per_minute=60, burst=3)
    ok_count = sum(1 for _ in range(5) if rl.allow("u1")[0])
    assert ok_count == 3  # burst
    ok, info = rl.allow("u1")
    assert ok is False and "retry_after_s" in info


def test_audit_log():
    with tempfile.TemporaryDirectory() as td:
        log = AuditLog(path=Path(td) / "audit.jsonl", enabled=True)
        log.record("login", actor="demo", outcome="ok")
        log.record("permission", actor="demo", resource="desktop", outcome="deny")
        rows = log.read_file()
        assert len(rows) >= 2
        assert rows[-1]["action"] in ("login", "permission")


def test_backup_restore_integrity():
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        data.mkdir()
        (data / "goals").mkdir()
        (data / "goals" / "goal_x.json").write_text('{"id":"goal_x"}')
        (data / "learning").mkdir()
        (data / "learning" / "learning_state.json").write_text('{"n":1}')
        bm = BackupManager(data, backup_dir=Path(td) / "backups")
        meta = bm.create(label="test")
        assert Path(meta["path"]).exists()
        ver = bm.verify(Path(meta["path"]))
        assert ver["ok"] is True
        # restore to new dir
        target = Path(td) / "restored"
        result = bm.restore(Path(meta["path"]), target_dir=target)
        assert result["ok"] is True
        assert (target / "goals" / "goal_x.json").exists()


def test_worker_quarantine():
    with tempfile.TemporaryDirectory() as td:
        wm = WorkerManager(None, persist_dir=Path(td), max_workers_local=1)
        wid = list(wm.workers.keys())[0]
        w = wm.workers[wid]
        w.total_failed = 5
        assert wm.maybe_quarantine(wid, threshold=5) is True
        assert wm.workers[wid].status.value == "disabled"
        assert wm.workers[wid].meta.get("quarantined") is True


def test_integrity_and_resources():
    with tempfile.TemporaryDirectory() as td:
        r = integrity_check(td)
        assert r["ok"] is True
        assert "pid" in resource_usage()


def test_orchestrator_ops_wired():
    with tempfile.TemporaryDirectory() as td:
        set_config(Config(profile="testing", overrides={"data_dir": td, "backup_dir": td + "/b"}))
        orch = Orchestrator(memory=Memory(session_id="ops1", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        assert hasattr(orch, "audit")
        assert hasattr(orch, "rate_limiter")
        assert hasattr(orch, "backups")
        meta = orch.backups.create(label="orch")
        assert Path(meta["path"]).exists()


def test_api_rate_limit_integration():
    from service.app import PearService
    with tempfile.TemporaryDirectory() as td:
        set_config(Config(profile="testing", overrides={
            "data_dir": td,
            "backup_dir": td + "/b",
            "rate_limit_per_minute": 60,
            "rate_limit_burst": 2,
        }))
        svc = PearService(data_root=Path(td))
        svc.rate_limiter.configure(60, burst=2)
        svc.sessions.llm = EchoLLM()
        _, login = svc.handle_route(
            "POST", "/auth/login", {},
            json.dumps({"username": "demo", "password": "demo"}).encode(),
        )
        token = login["token"]
        headers = {"Authorization": f"Bearer {token}"}
        codes = []
        for _ in range(4):
            status, _ = svc.handle_route(
                "POST", "/v1/chat", headers,
                json.dumps({"message": "hi"}).encode(),
            )
            codes.append(status)
        assert 429 in codes


def test_admin_cli_config():
    from scripts.admin_cli import main
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["--profile", "testing", "config"])
    assert "profile" in buf.getvalue()


if __name__ == "__main__":
    test_config_profiles()
    print("  ✓ config profiles")
    test_config_validation_and_reload()
    print("  ✓ config reload")
    test_structured_logging_correlation()
    print("  ✓ logging")
    test_rate_limiter()
    print("  ✓ rate limit")
    test_audit_log()
    print("  ✓ audit")
    test_backup_restore_integrity()
    print("  ✓ backup/restore")
    test_worker_quarantine()
    print("  ✓ quarantine")
    test_integrity_and_resources()
    print("  ✓ integrity")
    test_orchestrator_ops_wired()
    print("  ✓ orchestrator")
    test_api_rate_limit_integration()
    print("  ✓ api rate limit")
    test_admin_cli_config()
    print("  ✓ admin cli")
    print("All v2.40 ops tests passed.")
