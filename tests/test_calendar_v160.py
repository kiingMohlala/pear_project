"""Calendar agent regression tests (v1.60)."""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.calendar_agent import (
    CalendarAgent,
    parse_event_nl,
    events_overlap,
    CalEvent,
)
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent
from evaluation.engine import EvaluationEngine


def test_parse_event_nl():
    p = parse_event_nl("schedule team sync tomorrow at 2pm for 45 minutes")
    assert p["start"].hour == 14
    assert (p["end"] - p["start"]).total_seconds() == 45 * 60
    assert "team sync" in p["title"].lower() or "sync" in p["title"].lower()


def test_overlap():
    a = CalEvent("1", "A", datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 11, 0))
    b = CalEvent("2", "B", datetime(2026, 1, 1, 10, 30), datetime(2026, 1, 1, 11, 30))
    c = CalEvent("3", "C", datetime(2026, 1, 1, 11, 0), datetime(2026, 1, 1, 12, 0))
    assert events_overlap(a, b)
    assert not events_overlap(a, c)


def test_schedule_conflict_and_agenda():
    agent = CalendarAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="cal1")
    r = agent.think("schedule planning tomorrow at 10am for 60 minutes")
    assert r["ok"]
    r2 = agent.think("schedule overflow tomorrow at 10:30am for 30 minutes")
    assert r2["ok"] and r2.get("conflicts")
    ag = agent.think("agenda 3 days")
    assert ag["ok"] and ag.get("events")


def test_recurring_and_free_time():
    agent = CalendarAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="cal2")
    r = agent.think("schedule standup daily at 9am for 15 minutes")
    assert r["ok"]
    assert sum(1 for e in agent.calendar_events if e.recurrence == "daily") >= 2
    ft = agent.think("free time tomorrow")
    assert ft["ok"]


def test_planner_routes():
    orch = Orchestrator(memory=Memory(session_id="cal3"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(CalendarAgent(llm=EchoLLM()))
    task = orch.plan("schedule a meeting tomorrow and show my agenda")
    assert task.assigned_agent == "calendar"


def test_eval_suite():
    eng = EvaluationEngine()
    report = eng.run(suites=["calendar"], save_history=False, compare_baseline=False)
    assert report.suites["calendar"].success_rate >= 0.8


if __name__ == "__main__":
    test_parse_event_nl()
    print("  ✓ parse")
    test_overlap()
    print("  ✓ overlap")
    test_schedule_conflict_and_agenda()
    print("  ✓ schedule/conflict")
    test_recurring_and_free_time()
    print("  ✓ recurring/free")
    test_planner_routes()
    print("  ✓ planner")
    test_eval_suite()
    print("  ✓ eval")
    print("All v1.60 calendar tests passed.")
