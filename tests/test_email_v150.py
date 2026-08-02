"""Email agent regression tests (v1.50)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.email_agent import EmailAgent, priority_score, EmailMessage, demo_mailbox
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent
from evaluation.engine import EvaluationEngine


def test_priority_order():
    box = demo_mailbox()
    box.sort(key=lambda m: m.priority, reverse=True)
    assert box[0].priority >= box[-1].priority
    assert priority_score(box[0]) >= priority_score(box[-1])


def test_sync_inbox_search():
    agent = EmailAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="e1")
    r = agent.think("sync inbox")
    assert r["ok"] and r["count"] >= 5
    r2 = agent.think("inbox")
    assert r2["ok"] and "Inbox" in r2["reply"]
    r3 = agent.think("email search invoice")
    assert r3["ok"] and r3["count"] >= 1


def test_thread_and_draft():
    agent = EmailAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="e2")
    agent.think("sync inbox")
    r = agent.think("summarize thread Phoenix")
    assert r["ok"] and r["count"] >= 1
    d = agent.think("draft email reply")
    assert d["ok"] and "Subject:" in (d.get("draft") or "")


def test_followups():
    agent = EmailAgent(llm=EchoLLM())
    agent.memory = Memory(session_id="e3")
    agent.think("sync inbox")
    r = agent.think("follow-ups")
    assert r["ok"] and r["count"] >= 1


def test_planner_routes():
    orch = Orchestrator(memory=Memory(session_id="e4"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(EmailAgent(llm=EchoLLM()))
    task = orch.plan("show my inbox and prioritize urgent emails")
    assert task.assigned_agent == "email"


def test_eval_suite():
    eng = EvaluationEngine()
    report = eng.run(suites=["email"], save_history=False, compare_baseline=False)
    assert report.suites["email"].success_rate >= 0.8


if __name__ == "__main__":
    test_priority_order()
    print("  ✓ priority")
    test_sync_inbox_search()
    print("  ✓ sync/search")
    test_thread_and_draft()
    print("  ✓ thread/draft")
    test_followups()
    print("  ✓ followups")
    test_planner_routes()
    print("  ✓ planner")
    test_eval_suite()
    print("  ✓ eval")
    print("All v1.50 email tests passed.")
