"""
Planner + Orchestrator.

User → Planner → (score agents + planner memory bias) → Task → Agent

Rules:
  - Agents never call each other; they request subtasks via the planner.
  - Tools live in the ToolRegistry; agents only hold allowed names.
  - Every significant step emits an Event.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .memory import Memory
from .task import Task, TaskStatus, TaskPriority
from .events import EventBus, EventType
from .tool_registry import ToolRegistry, build_default_registry
from .planner_memory import PlannerMemory
from .llm import BaseLLM, create_llm


class Orchestrator:
    def __init__(
        self,
        memory: Optional[Memory] = None,
        registry: Optional[ToolRegistry] = None,
        events: Optional[EventBus] = None,
        llm: Optional[BaseLLM] = None,
    ):
        self.memory = memory or Memory()
        self.registry = registry or build_default_registry()
        self.events = events or EventBus()
        self.planner_memory = PlannerMemory()
        self.llm = llm or create_llm()

        self.agents: Dict[str, Any] = {}
        self.default_agent_name: Optional[str] = None
        self.task_log: List[Task] = []

    def register(self, agent: Any, default: bool = False) -> None:
        self.agents[agent.name] = agent
        agent.memory = self.memory
        agent.registry = self.registry
        agent.events = self.events
        agent.planner = self
        if default or self.default_agent_name is None:
            self.default_agent_name = agent.name

    def list_agents(self) -> List[str]:
        return list(self.agents.keys())

    def agent_catalog(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": a.name,
                "description": getattr(a, "description", ""),
                "capabilities": list(getattr(a, "capabilities", [])),
                "allowed_tools": list(getattr(a, "allowed_tools", [])),
            }
            for a in self.agents.values()
        ]

    def plan(
        self,
        objective: str,
        *,
        required_capabilities: Optional[List[str]] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        preferred_agent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        parent_task: Optional[Task] = None,
    ) -> Task:
        task = Task(
            objective=objective.strip(),
            priority=priority,
            required_capabilities=required_capabilities or [],
            metadata=metadata or {},
            parent_id=parent_task.id if parent_task else None,
        )

        self.events.emit(EventType.TASK_CREATED, {
            "task_id": task.id,
            "objective": task.objective,
            "parent_id": task.parent_id,
        }, source="planner")

        if preferred_agent and preferred_agent in self.agents:
            task.assign(preferred_agent)
            self._record_and_emit(task, {preferred_agent: 1.0})
            self.task_log.append(task)
            return task

        scores: Dict[str, float] = {}
        for name, agent in self.agents.items():
            raw = agent.can_handle(task)
            bias = self.planner_memory.bias_for(name)
            scores[name] = max(0.0, raw + bias)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        if ranked and ranked[0][1] > 0:
            best_name = ranked[0][0]
            task.assign(best_name)
            task.metadata["planner_scores"] = scores
        elif self.default_agent_name:
            task.assign(self.default_agent_name)
            task.metadata["planner_fallback"] = True
            task.metadata["planner_scores"] = scores
        else:
            task.fail("No agents registered")
            self.events.emit(EventType.TASK_FAILED, {
                "task_id": task.id,
                "error": "No agents registered",
            }, source="planner")
            self.task_log.append(task)
            return task

        self._record_and_emit(task, scores)
        self.task_log.append(task)
        return task

    def _record_and_emit(self, task: Task, scores: Dict[str, float]) -> None:
        self.planner_memory.record_decision(
            objective=task.objective,
            chosen_agent=task.assigned_agent or "",
            scores=scores,
            task_id=task.id,
        )
        self.events.emit(EventType.AGENT_SELECTED, {
            "task_id": task.id,
            "agent": task.assigned_agent,
            "scores": scores,
        }, source="planner")

    def run(self, task: Task, **kwargs) -> Dict[str, Any]:
        if task.status == TaskStatus.FAILED:
            return {"ok": False, "error": task.error, "task_id": task.id}

        agent_name = task.assigned_agent
        if not agent_name or agent_name not in self.agents:
            task.fail("No agent assigned")
            return {"ok": False, "error": "No agent assigned", "task_id": task.id}

        agent = self.agents[agent_name]
        result = agent.think(task.objective, task=task, **kwargs)
        self.planner_memory.mark_outcome(task.id, success=bool(result.get("ok")))
        return result

    def route(
        self,
        user_input: str,
        preferred: Optional[str] = None,
        required_capabilities: Optional[List[str]] = None,
        parent_task: Optional[Task] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        user_input = user_input.strip()
        if not user_input:
            return {"ok": False, "error": "Empty input"}

        task = self.plan(
            user_input,
            required_capabilities=required_capabilities,
            preferred_agent=preferred,
            parent_task=parent_task,
        )
        return self.run(task, **kwargs)

    def recent_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.task_log[-limit:]]

    def recent_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events.recent(limit)]
