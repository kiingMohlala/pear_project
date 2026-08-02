"""Regression tests for v0.34 tracing & metrics."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tracing import Tracer, get_tracer, set_tracer
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from core.events import EventType
from agents import PersonalAgent


def test_nested_spans():
    tr = Tracer()
    set_tracer(tr)
    with tr.request("demo") as trace:
        with tr.span("planner", kind="planner"):
            time.sleep(0.01)
            with tr.span("llm", kind="llm", model="echo"):
                time.sleep(0.01)
    assert trace.ended_at is not None
    spans = list(trace.spans.values())
    assert len(spans) >= 3
    kinds = {s.kind for s in spans}
    assert "planner" in kinds and "llm" in kinds
    # child parent linkage
    llm = next(s for s in spans if s.kind == "llm")
    assert llm.parent_id is not None
    assert llm.duration_ms is not None and llm.duration_ms >= 0


def test_trace_persistence():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "traces.sqlite"
        tr = Tracer(persist_path=path)
        with tr.request("persist-me"):
            with tr.span("work", kind="internal"):
                pass
        tid = list(tr._traces.keys())[0]
        # New tracer loads from DB via get_trace
        tr2 = Tracer(persist_path=path)
        loaded = tr2.get_trace(tid)
        assert loaded is not None
        assert loaded["name"] == "persist-me"
        assert len(loaded["spans"]) >= 1


def test_timing_consistency():
    tr = Tracer()
    with tr.request("timing"):
        with tr.span("a", kind="internal"):
            time.sleep(0.02)
    t = list(tr._traces.values())[0]
    assert t.duration_ms is not None and t.duration_ms >= 15
    for s in t.spans.values():
        if s.duration_ms is not None:
            assert s.duration_ms >= 0
            assert s.ended_at >= s.started_at


def test_orchestrator_creates_trace():
    orch = Orchestrator(memory=Memory(session_id="tr1"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    set_tracer(orch.tracer)
    r = orch.route("hello tracing")
    assert r.get("ok")
    assert r.get("trace_id")
    tr = orch.tracer.get_trace(r["trace_id"])
    assert tr is not None
    kinds = {s["kind"] for s in tr["spans"]}
    assert "request" in kinds or any(s["name"] == "request" for s in tr["spans"])
    assert "planner" in kinds


def test_events_carry_trace_ids():
    orch = Orchestrator(memory=Memory(session_id="tr2"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    set_tracer(orch.tracer)
    orch.route("hello events")
    with_ids = [e for e in orch.events.history if e.trace_id]
    assert len(with_ids) >= 1


def test_metrics_aggregate():
    tr = Tracer()
    set_tracer(tr)
    for i in range(3):
        with tr.request(f"r{i}"):
            with tr.span("llm", kind="llm"):
                time.sleep(0.005)
    m = tr.summary_metrics()
    assert m["requests"] == 3
    assert m["success_rate"] == 1.0
    assert m["latency_ms"]["request_avg"] > 0


def test_reconstruction_tree():
    tr = Tracer()
    with tr.request("tree"):
        with tr.span("planner", kind="planner"):
            with tr.span("retrieval", kind="retrieval"):
                pass
            with tr.span("llm", kind="llm"):
                pass
    data = list(tr._traces.values())[0].to_dict()
    by_id = {s["id"]: s for s in data["spans"]}
    # every non-root has parent in set
    for s in data["spans"]:
        if s["id"] == data["root_span_id"]:
            continue
        assert s["parent_id"] in by_id or s["parent_id"] == data["root_span_id"]


if __name__ == "__main__":
    test_nested_spans()
    print("  ✓ nested spans")
    test_trace_persistence()
    print("  ✓ persistence")
    test_timing_consistency()
    print("  ✓ timing")
    test_orchestrator_creates_trace()
    print("  ✓ orchestrator trace")
    test_events_carry_trace_ids()
    print("  ✓ event trace ids")
    test_metrics_aggregate()
    print("  ✓ metrics")
    test_reconstruction_tree()
    print("  ✓ reconstruction")
    print("All v0.34 tracing tests passed.")
