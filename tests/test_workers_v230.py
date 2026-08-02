"""Distributed worker runtime regression tests (v2.30)."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.workers import (
    WorkerManager,
    WorkerStatus,
    DispatchStatus,
    infer_capabilities,
)
from agents import PersonalAgent


def make_orch(td: Path):
    orch = Orchestrator(memory=Memory(session_id="w1", persist_dir=td), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.workers = WorkerManager(orch, persist_dir=td / "workers", max_workers_local=2)
    return orch


def test_infer_capabilities():
    assert "legal" in infer_capabilities("review this NDA contract")
    assert "finance" in infer_capabilities("analyse my budget")
    assert "browser" in infer_capabilities("open url https://example.com")
    assert "general" in infer_capabilities("hello")


def test_register_and_list():
    with tempfile.TemporaryDirectory() as tmp:
        wm = WorkerManager(None, persist_dir=Path(tmp))
        w = wm.register_worker("gpu-1", {"gpu", "general"}, max_concurrency=1)
        assert w.id in wm.workers
        workers = wm.list_workers()
        assert any(x["name"] == "gpu-1" for x in workers)
        assert any(x["name"] == "local-default" for x in workers)


def test_capability_routing():
    with tempfile.TemporaryDirectory() as tmp:
        wm = WorkerManager(None, persist_dir=Path(tmp), max_workers_local=1)
        gpu = wm.register_worker("gpu-node", {"gpu", "general"}, max_concurrency=2)
        selected = wm.select_worker(["gpu"])
        assert selected is not None
        assert selected.id == gpu.id


def test_local_dispatch_success():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        rec = orch.workers.dispatch("hello from worker", required_capabilities=["general"])
        done = orch.workers.wait(rec.id, timeout=10)
        assert done.status == DispatchStatus.SUCCEEDED
        assert done.result is not None


def test_disable_and_failover():
    with tempfile.TemporaryDirectory() as tmp:
        wm = WorkerManager(None, persist_dir=Path(tmp), max_workers_local=2)
        # disable default
        default_id = [w.id for w in wm.workers.values() if w.name == "local-default"][0]
        wm.disable(default_id)
        backup = wm.register_worker("backup", {"general"}, max_concurrency=2)
        selected = wm.select_worker(["general"])
        assert selected is not None
        assert selected.id == backup.id


def test_drain_worker():
    with tempfile.TemporaryDirectory() as tmp:
        wm = WorkerManager(None, persist_dir=Path(tmp))
        wid = list(wm.workers.keys())[0]
        w = wm.drain(wid)
        assert w.status == WorkerStatus.DRAINING
        assert wm.select_worker(["general"]) is None or wm.select_worker(["general"]).id != wid


def test_heartbeat_recovery():
    with tempfile.TemporaryDirectory() as tmp:
        wm = WorkerManager(None, persist_dir=Path(tmp), heartbeat_timeout_s=0.05)
        remote = wm.register_worker("remote", {"general"}, endpoint="http://127.0.0.1:9")
        remote.last_heartbeat = time.time() - 10
        offline = wm.check_heartbeats()
        assert remote.id in offline
        assert remote.status == WorkerStatus.OFFLINE
        wm.heartbeat(remote.id)
        assert remote.status == WorkerStatus.ONLINE


def test_retry_on_failure():
    with tempfile.TemporaryDirectory() as tmp:
        wm = WorkerManager(None, persist_dir=Path(tmp), max_workers_local=2)

        attempts = {"n": 0}

        def flaky(objective: str):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("transient")
            return {"ok": True, "reply": "recovered"}

        rec = wm.dispatch("task", required_capabilities=["general"], max_attempts=3, execute_fn=flaky)
        done = wm.wait(rec.id, timeout=10)
        assert done.status == DispatchStatus.SUCCEEDED
        assert done.attempts >= 2


def test_concurrent_dispatch():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        recs = [
            orch.workers.dispatch(f"note: concurrent {i}", required_capabilities=["general"])
            for i in range(3)
        ]
        results = [orch.workers.wait(r.id, timeout=15) for r in recs]
        assert sum(1 for r in results if r.status == DispatchStatus.SUCCEEDED) >= 2


def test_metrics():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orch(Path(tmp))
        rec = orch.workers.dispatch("note: metrics", required_capabilities=["general"])
        orch.workers.wait(rec.id, timeout=10)
        m = orch.workers.metrics_snapshot()
        assert m["dispatched"] >= 1
        assert "avg_dispatch_latency_ms" in m


def test_single_node_compatible():
    """Default orchestrator still works without explicit worker usage."""
    with tempfile.TemporaryDirectory() as tmp:
        orch = Orchestrator(memory=Memory(session_id="w2", persist_dir=Path(tmp)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        r = orch.route("hello")
        assert r.get("ok") is True or "reply" in r


if __name__ == "__main__":
    test_infer_capabilities()
    print("  ✓ capabilities")
    test_register_and_list()
    print("  ✓ register")
    test_capability_routing()
    print("  ✓ routing")
    test_local_dispatch_success()
    print("  ✓ local dispatch")
    test_disable_and_failover()
    print("  ✓ failover")
    test_drain_worker()
    print("  ✓ drain")
    test_heartbeat_recovery()
    print("  ✓ heartbeat")
    test_retry_on_failure()
    print("  ✓ retry")
    test_concurrent_dispatch()
    print("  ✓ concurrent")
    test_metrics()
    print("  ✓ metrics")
    test_single_node_compatible()
    print("  ✓ single-node")
    print("All v2.30 worker tests passed.")
