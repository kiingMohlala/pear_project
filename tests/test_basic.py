"""Smoke tests for PEAR core (planner, tools, events, tasks)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.orchestrator import Orchestrator
from core.task import Task, TaskStatus
from core.events import EventType
from core.tool_registry import build_default_registry
from agents import PersonalAgent, DesktopAgent, FinanceAgent, LegalAgent


def test_memory_layers():
    m = Memory(session_id="test")
    m.working.add("user", "hello")
    m.long_term.set_pref("name", "Tester")
    m.knowledge.add_note("t", "body")
    assert len(m.working.messages) == 1
    assert m.long_term.get_pref("name") == "Tester"
    assert len(m.knowledge.list_notes()) == 1


def test_tool_registry():
    reg = build_default_registry()
    assert reg.has("open_application")
    assert reg.has("summarize_text")
    names = reg.names()
    assert "search_files" in names


def test_planner_routes():
    orch = Orchestrator(memory=Memory(session_id="t2"))
    orch.register(PersonalAgent(), default=True)
    orch.register(DesktopAgent())
    orch.register(FinanceAgent())
    orch.register(LegalAgent())

    assert orch.plan("open app calculator").assigned_agent == "desktop"
    assert orch.plan("hello there").assigned_agent == "personal"
    assert orch.plan("analyse my budget").assigned_agent == "finance"
    assert orch.plan("review this NDA contract").assigned_agent == "legal"


def test_events_emitted():
    orch = Orchestrator(memory=Memory(session_id="t3"))
    orch.register(PersonalAgent(), default=True)
    r = orch.route("hello")
    assert r["ok"] is True
    types = [e.type for e in orch.events.history]
    assert EventType.TASK_CREATED in types
    assert EventType.AGENT_SELECTED in types
    assert EventType.TASK_STARTED in types
    assert EventType.TASK_COMPLETED in types


def test_planner_memory_learns():
    orch = Orchestrator(memory=Memory(session_id="t4"))
    orch.register(PersonalAgent(), default=True)
    orch.register(DesktopAgent())
    orch.route("hello")
    orch.route("open app xed")
    summary = orch.planner_memory.summary()
    assert summary["total_decisions"] >= 2


def test_agent_cannot_use_foreign_tool():
    orch = Orchestrator(memory=Memory(session_id="t5"))
    orch.register(PersonalAgent(), default=True)
    agent = orch.agents["personal"]
    try:
        agent.use_tool("open_application", "calc")
        assert False, "should have raised"
    except PermissionError:
        pass


def test_streaming_matches_non_streaming():
    orch = Orchestrator(memory=Memory(session_id="t7"))
    orch.register(PersonalAgent(), default=True)

    chunks = []
    streamed = orch.route("tell me something", on_token=chunks.append)
    assert streamed["ok"] is True
    assert streamed.get("streamed") is True
    assert len(chunks) > 1, "expected more than one streamed chunk"
    assert "".join(chunks) == streamed["reply"]

    plain = orch.route("tell me something")
    assert plain["ok"] is True
    assert "streamed" not in plain


def test_full_note():
    orch = Orchestrator(memory=Memory(session_id="t6"))
    orch.register(PersonalAgent(), default=True)
    r = orch.route("note: buy milk")
    assert r["ok"] is True
    assert "task_id" in r
    assert len(orch.memory.list_notes()) == 1


if __name__ == "__main__":
    test_memory_layers()
    test_tool_registry()
    test_planner_routes()
    test_events_emitted()
    test_planner_memory_learns()
    test_agent_cannot_use_foreign_tool()
    test_streaming_matches_non_streaming()
    test_full_note()
    print("All smoke tests passed.")
