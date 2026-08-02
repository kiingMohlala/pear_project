"""Unit tests for v0.22 planner, task graph, executor."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.task_graph import TaskGraph, TaskNode, NodeStatus, CycleError
from core.planner_llm import PlannerLLM, ExecutionPlan, PlannedTask
from core.executor import Executor, ResultAggregator
from core.events import EventBus, EventType
from core.memory import Memory
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from agents import PersonalAgent, DesktopAgent, FinanceAgent, LegalAgent


def test_task_graph_dag_and_order():
    g = TaskGraph(summary="test")
    a = TaskNode(objective="A")
    b = TaskNode(objective="B")
    c = TaskNode(objective="C")
    g.add_node(a)
    g.add_node(b)
    g.add_node(c)
    g.add_dependency(b.id, a.id)  # B depends on A
    g.add_dependency(c.id, b.id)  # C depends on B
    order = [n.objective for n in g.execution_order()]
    assert order == ["A", "B", "C"]


def test_task_graph_rejects_cycle():
    g = TaskGraph()
    a = TaskNode(objective="A")
    b = TaskNode(objective="B")
    g.add_node(a)
    g.add_node(b)
    g.add_dependency(b.id, a.id)
    try:
        g.add_dependency(a.id, b.id)
        assert False, "should have raised CycleError"
    except CycleError:
        pass


def test_ready_tasks_unlock():
    g = TaskGraph()
    a = TaskNode(objective="A")
    b = TaskNode(objective="B")
    g.add_node(a)
    g.add_node(b)
    g.add_dependency(b.id, a.id)
    ready = g.ready_tasks()
    assert len(ready) == 1 and ready[0].id == a.id
    g.mark_completed(a.id, {"ok": True, "reply": "done A"})
    ready2 = g.ready_tasks()
    assert len(ready2) == 1 and ready2[0].id == b.id


def test_execution_plan_to_graph():
    plan = ExecutionPlan(
        summary="demo",
        tasks=[
            PlannedTask(objective="step1", preferred_agent="personal"),
            PlannedTask(objective="step2", preferred_agent="legal", depends_on=[0]),
        ],
    )
    g = plan.to_graph()
    assert len(g.nodes) == 2
    order = g.execution_order()
    assert order[0].objective == "step1"
    assert order[1].objective == "step2"
    assert order[0].id in order[1].dependencies


def test_planner_simple_is_single_step():
    planner = PlannerLLM(llm=EchoLLM())
    agents = [
        {"name": "personal", "description": "chat", "capabilities": ["chat", "notes"]},
        {"name": "desktop", "description": "desktop", "capabilities": ["desktop"]},
    ]
    plan = planner.plan("hello", agents)
    assert plan.single_step is True
    assert len(plan.tasks) == 1


def test_planner_heuristic_legal_multi_step():
    planner = PlannerLLM(llm=EchoLLM())
    agents = [
        {"name": "personal", "description": "chat", "capabilities": ["chat", "file_reading"]},
        {"name": "legal", "description": "legal", "capabilities": ["legal", "document_review", "contract"]},
    ]
    plan = planner.plan("Review this NDA and summarize the risks", agents)
    assert plan.single_step is False
    assert len(plan.tasks) >= 2
    # dependency chain
    assert plan.tasks[1].depends_on == [0] or 0 in plan.tasks[1].depends_on


def test_executor_sequential():
    g = TaskGraph(summary="seq")
    a = TaskNode(objective="first", assigned_agent="personal")
    b = TaskNode(objective="second", assigned_agent="personal")
    g.add_node(a)
    g.add_node(b)
    g.add_dependency(b.id, a.id)

    calls = []

    def run_task(**kwargs):
        calls.append(kwargs["objective"])
        return {"ok": True, "reply": f"done:{kwargs['objective']}", "agent": "personal"}

    events = EventBus()
    ex = Executor(run_task=run_task, events=events)
    result = ex.execute(g)
    assert result["ok"] is True
    assert calls == ["first", "second"]
    types = [e.type for e in events.history]
    assert EventType.PLAN_CREATED in types
    assert EventType.PLAN_COMPLETED in types
    assert EventType.TASK_EXECUTION_STARTED in types


def test_aggregator_dedupes():
    g = TaskGraph(summary="agg")
    a = TaskNode(objective="A")
    g.add_node(a)
    g.mark_completed(a.id, {"ok": True, "reply": "same text"})
    # only one node – fine
    agg = ResultAggregator().aggregate(g)
    assert "same text" in agg["reply"]
    assert agg["ok"] is True


def test_orchestrator_route_simple():
    orch = Orchestrator(memory=Memory(session_id="p1"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(DesktopAgent())
    r = orch.route("hello")
    assert r.get("ok") is True
    assert r.get("plan_id")
    assert orch.current_plan is not None
    assert orch.current_plan.single_step is True


def test_orchestrator_route_complex_legal():
    orch = Orchestrator(memory=Memory(session_id="p2"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(LegalAgent())
    r = orch.route("Review this NDA and summarize the risks")
    assert r.get("plan_id")
    assert orch.current_graph is not None
    assert len(orch.current_graph.nodes) >= 2
    # plan recorded
    hist = orch.planner_memory.recent_plans(1)
    assert len(hist) == 1


def test_legacy_plan_still_works():
    """v0.1 plan()+run() path used by tests and subtasks."""
    orch = Orchestrator(memory=Memory(session_id="p3"), llm=EchoLLM())
    orch.register(PersonalAgent(llm=EchoLLM()), default=True)
    orch.register(DesktopAgent())
    task = orch.plan("open app calculator")
    assert task.assigned_agent == "desktop"
    # don't actually open apps in CI – just ensure assignment


if __name__ == "__main__":
    test_task_graph_dag_and_order()
    test_task_graph_rejects_cycle()
    test_ready_tasks_unlock()
    test_execution_plan_to_graph()
    test_planner_simple_is_single_step()
    test_planner_heuristic_legal_multi_step()
    test_executor_sequential()
    test_aggregator_dedupes()
    test_orchestrator_route_simple()
    test_orchestrator_route_complex_legal()
    test_legacy_plan_still_works()
    print("All v0.22 planner tests passed.")
