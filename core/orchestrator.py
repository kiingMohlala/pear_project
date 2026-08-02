"""
Planner + Orchestrator (v0.22).

User → PlannerLLM → ExecutionPlan → TaskGraph → Executor → Aggregation → Response

Simple requests still collapse to a single task (backwards compatible).
Agents never call each other; collaboration is only via planner subtasks.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .memory import Memory
from .task import Task, TaskStatus, TaskPriority
from .events import EventBus, EventType
from .tool_registry import ToolRegistry, build_default_registry
from .planner_memory import PlannerMemory
from .llm import BaseLLM, create_llm
from .planner_llm import PlannerLLM, ExecutionPlan
from .task_graph import TaskGraph
from .executor import Executor, ResultAggregator
from .job import Job, JobStatus, JobPriority
from .job_manager import JobManager
from .tracing import Tracer, get_tracer, set_tracer
from .workflow import WorkflowRunner, Workflow, WorkflowStep
from .connectors import build_default_connectors, ConnectorRegistry
from .media import MediaManager
from .plugins import PluginManager
from .voice import VoiceAssistant
from .collaboration import CollaborationManager
from .goals import GoalManager
from .learning import LearningEngine
from .workers import WorkerManager
from .memory_intelligence import MemoryIntelligence


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
        self.planner_llm = PlannerLLM(llm=self.llm)

        self.agents: Dict[str, Any] = {}
        self.default_agent_name: Optional[str] = None
        self.task_log: List[Task] = []

        # v0.22 state
        self.current_plan: Optional[ExecutionPlan] = None
        self.current_graph: Optional[TaskGraph] = None
        self.last_aggregated: Optional[Dict[str, Any]] = None

        self.executor = Executor(
            run_task=self._run_leaf_task,
            events=self.events,
            aggregator=ResultAggregator(),
        )

        # Background jobs (v0.33) – optional persist_dir via memory
        persist = None
        if getattr(self.memory, "persist_dir", None):
            from pathlib import Path as _P
            persist = _P(self.memory.persist_dir) / "jobs.sqlite"
        self.jobs = JobManager(
            events=self.events,
            persist_path=persist,
            runner=self._run_job,
            max_workers=1,
        )

        trace_path = None
        if getattr(self.memory, "persist_dir", None):
            from pathlib import Path as _P
            trace_path = _P(self.memory.persist_dir) / "traces.sqlite"
        self.tracer = Tracer(persist_path=trace_path)
        set_tracer(self.tracer)

        wf_dir = None
        if getattr(self.memory, "persist_dir", None):
            from pathlib import Path as _P
            wf_dir = _P(self.memory.persist_dir) / "workflows"
        self.workflows = WorkflowRunner(self, persist_dir=wf_dir)

        try:
            from .desktop import Workspace
            ws = Workspace()
        except Exception:
            ws = None
        self.connectors = build_default_connectors(workspace=ws)
        media_dir = None
        if getattr(self.memory, "persist_dir", None):
            from pathlib import Path as _P
            media_dir = _P(self.memory.persist_dir) / "media"
        self.media = MediaManager(knowledge=self.memory.knowledge, media_dir=media_dir)
        self.plugin_commands = {}
        plug_dir = None
        if getattr(self.memory, "persist_dir", None):
            from pathlib import Path as _P
            # prefer repo plugins/ directory
        self.plugins = PluginManager(self)
        self.voice = VoiceAssistant(orchestrator=self)
        self.collaboration = CollaborationManager(self)
        self.goals = GoalManager(self)
        self.learning = LearningEngine(self)
        self.workers = WorkerManager(self)
        self.memory_intel = getattr(self.memory, "intelligence", None) or MemoryIntelligence(self.memory)
        if getattr(self.memory, "intelligence", None) is None:
            self.memory.intelligence = self.memory_intel
        else:
            self.memory_intel = self.memory.intelligence
            self.memory_intel.attach(self.memory)
        try:
            self.plugins.load_all()
        except Exception:
            pass

    # ── registration ──────────────────────────────────────────────

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

    # ── legacy single-task plan (kept for tests / direct use) ─────

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
        """Select a single agent and return an assigned Task (v0.1 path)."""
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

    # ── v0.22 intelligent route ───────────────────────────────────

    def route(
        self,
        user_input: str,
        preferred: Optional[str] = None,
        required_capabilities: Optional[List[str]] = None,
        parent_task: Optional[Task] = None,
        on_token=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Full pipeline: plan → graph → execute → aggregate.
        Simple objectives still become a one-node graph.

        If on_token is provided, tokens are streamed for the primary reply
        (single-step agent path, or final aggregated text for multi-step).
        """
        user_input = (user_input or "").strip()
        if not user_input:
            return {"ok": False, "error": "Empty input"}

        trace = self.tracer.start_trace(
            "request",
            kind="request",
            objective=user_input[:200],
            background=False,
        )

        # Forced single-agent path (subtasks / preferred)
        if preferred or required_capabilities or parent_task is not None:
            task = self.plan(
                user_input,
                required_capabilities=required_capabilities,
                preferred_agent=preferred,
                parent_task=parent_task,
            )
            result = self.run(task, on_token=on_token, **kwargs)
            if on_token is not None and result.get("ok"):
                result["streamed"] = True
            result["trace_id"] = trace.id
            self.tracer.end_trace(trace.id, status="ok" if result.get("ok") else "error")
            return result

        t0 = time.time()
        catalog = self.agent_catalog()
        with self.tracer.span("planner", kind="planner"):
            execution_plan = self.planner_llm.plan(user_input, catalog)
        self.current_plan = execution_plan

        graph = execution_plan.to_graph()
        for node in graph.nodes.values():
            if not node.assigned_agent:
                leaf = self.plan(
                    node.objective,
                    required_capabilities=node.required_capabilities or None,
                )
                node.assigned_agent = leaf.assigned_agent

        self.current_graph = graph

        # Single-step + streaming → run leaf directly so on_token reaches the agent
        if execution_plan.single_step and on_token is not None and len(graph.nodes) == 1:
            node = next(iter(graph.nodes.values()))
            result = self._run_leaf_task(
                objective=node.objective,
                preferred_agent=node.assigned_agent,
                required_capabilities=node.required_capabilities or None,
                node_id=node.id,
                plan_id=graph.plan_id,
                on_token=on_token,
                **kwargs,
            )
            graph.mark_running(node.id)
            if result.get("ok", True):
                graph.mark_completed(node.id, result)
            else:
                graph.mark_failed(node.id, result.get("error") or "failed")
            graph.completed_at = time.time()
            duration = time.time() - t0
            self.planner_memory.record_plan(
                plan_id=graph.plan_id,
                summary=graph.summary,
                objective=user_input,
                task_count=1,
                success=bool(result.get("ok")),
                duration_s=duration,
                reasoning=execution_plan.reasoning,
                single_step=True,
            )
            result = dict(result)
            result["streamed"] = True
            result.setdefault("plan_id", graph.plan_id)
            result["trace_id"] = trace.id
            self.last_aggregated = result
            self.tracer.end_trace(trace.id, status="ok" if result.get("ok") else "error")
            return result

        aggregated = self.executor.execute(graph)
        self.last_aggregated = aggregated

        duration = time.time() - t0
        self.planner_memory.record_plan(
            plan_id=graph.plan_id,
            summary=graph.summary,
            objective=user_input,
            task_count=len(graph.nodes),
            success=bool(aggregated.get("ok")),
            duration_s=duration,
            reasoning=execution_plan.reasoning,
            single_step=execution_plan.single_step,
        )

        aggregated.setdefault("agent", "planner")
        aggregated.setdefault("action", "plan_execute")
        if not aggregated.get("task_id"):
            results = aggregated.get("results") or []
            if len(results) == 1:
                leaf = results[0]
                leaf_result = leaf.get("result") or {}
                aggregated["task_id"] = leaf_result.get("task_id") or leaf.get("id")
                if leaf_result.get("agent"):
                    aggregated["agent"] = leaf_result["agent"]
                if leaf_result.get("action"):
                    aggregated["action"] = leaf_result["action"]

        # Multi-step streaming: emit final reply as synthetic chunks
        if on_token is not None and aggregated.get("reply"):
            text = aggregated["reply"]
            parts = text.split(" ")
            for i, part in enumerate(parts):
                chunk = part if i == len(parts) - 1 else part + " "
                on_token(chunk)
            aggregated["streamed"] = True

        aggregated["trace_id"] = trace.id
        self.tracer.end_trace(
            trace.id,
            status="ok" if aggregated.get("ok") else "error",
        )
        return aggregated

    def _run_leaf_task(
        self,
        *,
        objective: str,
        preferred_agent: Optional[str] = None,
        required_capabilities: Optional[List[str]] = None,
        parent_task_id: Optional[str] = None,
        node_id: str = "",
        plan_id: str = "",
        on_token=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Callback used by Executor for each ready node."""
        parent = None
        if parent_task_id:
            parent = Task(objective="parent", id=parent_task_id)

        task = self.plan(
            objective,
            preferred_agent=preferred_agent,
            required_capabilities=required_capabilities,
            parent_task=parent,
            metadata={"node_id": node_id, "plan_id": plan_id},
        )
        return self.run(task, on_token=on_token, **kwargs)

    # ── introspection ─────────────────────────────────────────────

    def recent_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.task_log[-limit:]]

    def recent_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events.recent(limit)]


    # ── v0.33 background jobs ─────────────────────────────────────

    def submit_job(
        self,
        objective: str,
        *,
        priority: str = "normal",
        background: bool = True,
        scheduled_at: Optional[float] = None,
        **schedule_kwargs,
    ):
        """
        Submit work as a background job (or run immediately if background=False).
        Agents are unchanged — the job runner calls route().
        """
        if not background:
            return self.route(objective)

        prio = JobPriority(priority) if not isinstance(priority, JobPriority) else priority
        if schedule_kwargs:
            job = self.jobs.schedule(
                objective,
                when=scheduled_at,
                priority=prio,
                **{k: v for k, v in schedule_kwargs.items() if k in (
                    "interval_s", "daily_hour", "weekly_weekday", "metadata"
                )},
            )
        else:
            job = self.jobs.enqueue(
                objective,
                priority=prio,
                scheduled_at=scheduled_at,
            )
        self.jobs.start()
        return {
            "ok": True,
            "job_id": job.id,
            "status": job.status.value,
            "reply": f"Queued job {job.id}: {objective[:80]}",
            "action": "job_queued",
        }

    def _run_job(self, job: Job):
        """Job runner – executes objective via the normal route pipeline."""
        wait_ms = None
        if job.started_at and job.created_at:
            wait_ms = max(0.0, (job.started_at - job.created_at) * 1000)
        with self.tracer.span(
            "job",
            kind="job",
            job_id=job.id,
            queue_wait_ms=wait_ms,
        ):
            self.jobs.set_progress(job.id, 0.1, "planning")
            result = self.route(job.objective)
            self.jobs.set_progress(job.id, 0.9, "finishing")
            if self.current_plan:
                job.plan_snapshot = self.current_plan.to_dict()
            if self.current_graph:
                job.graph_snapshot = self.current_graph.to_dict()
            return result

    def plan_snapshot(self) -> Optional[Dict[str, Any]]:
        if self.current_plan:
            return self.current_plan.to_dict()
        return None

    def graph_snapshot(self) -> Optional[Dict[str, Any]]:
        if self.current_graph:
            return self.current_graph.to_dict()
        return None
