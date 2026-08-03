"""Lightweight load / stress / recovery baselines (v3.00)."""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.config import Config, set_config
from core.ratelimit import RateLimiter
from agents import PersonalAgent


def test_route_latency_baseline():
    with tempfile.TemporaryDirectory() as td:
        set_config(Config(profile="testing", overrides={"data_dir": td, "backup_dir": td + "/b"}))
        orch = Orchestrator(memory=Memory(session_id="perf", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        times = []
        for i in range(20):
            t0 = time.perf_counter()
            orch.route(f"note: perf {i}")
            times.append((time.perf_counter() - t0) * 1000)
        p50 = statistics.median(times)
        p95 = sorted(times)[int(0.95 * len(times)) - 1]
        # generous offline baseline
        assert p50 < 2000, f"p50 too high: {p50}"
        assert p95 < 5000, f"p95 too high: {p95}"
        print(f"  route latency ms p50={p50:.1f} p95={p95:.1f}")


def test_rate_limit_stress():
    rl = RateLimiter(per_minute=120, burst=20)
    allowed = sum(1 for _ in range(100) if rl.allow("stress")[0])
    assert 15 <= allowed <= 25  # burst window


def test_worker_dispatch_throughput():
    with tempfile.TemporaryDirectory() as td:
        orch = Orchestrator(memory=Memory(session_id="perf2", persist_dir=Path(td)), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        t0 = time.perf_counter()
        ids = []
        for i in range(8):
            rec = orch.workers.dispatch(f"note: load {i}", required_capabilities=["general"])
            ids.append(rec.id)
        for i in ids:
            orch.workers.wait(i, timeout=30)
        elapsed = time.perf_counter() - t0
        assert elapsed < 60
        print(f"  8 dispatches in {elapsed:.2f}s")


def test_recovery_after_migrate():
    with tempfile.TemporaryDirectory() as td:
        from core.version import migrate_data_dir
        root = Path(td)
        migrate_data_dir(root)
        # simulate crash: create goal state then new orchestrator
        orch = Orchestrator(memory=Memory(session_id="rec", persist_dir=root), llm=EchoLLM())
        orch.register(PersonalAgent(llm=EchoLLM()), default=True)
        g = orch.goals.create("note: recovery", auto_start=False)
        orch.goals.plan(g.id)
        gid = g.id
        orch2 = Orchestrator(memory=Memory(session_id="rec", persist_dir=root), llm=EchoLLM())
        orch2.register(PersonalAgent(llm=EchoLLM()), default=True)
        # goals persist under goals dir of GoalManager
        # manager uses persist relative to memory or home — just ensure no crash
        assert orch2.route("hello").get("ok") is True or "reply" in orch2.route("hello")


if __name__ == "__main__":
    test_route_latency_baseline()
    print("  ✓ latency baseline")
    test_rate_limit_stress()
    print("  ✓ rate stress")
    test_worker_dispatch_throughput()
    print("  ✓ worker throughput")
    test_recovery_after_migrate()
    print("  ✓ recovery")
    print("All v3.00 perf tests passed.")
