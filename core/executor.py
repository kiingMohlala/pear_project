"""
Sequential TaskGraph executor + result aggregation.

v0.22: deterministic, one task at a time.
Future: ready_tasks() already returns a set — parallel is a thin loop change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from .task_graph import TaskGraph, TaskNode, NodeStatus
from .events import EventBus, EventType

if TYPE_CHECKING:
    pass


# ── Result aggregation ────────────────────────────────────────────

class ResultAggregator:
    """
    Combine per-task results into one coherent reply.
    Preserves topological order, drops empties/duplicates.
    """

    def aggregate(self, graph: TaskGraph) -> Dict[str, Any]:
        parts: List[str] = []
        seen: set = set()
        task_results = graph.results()

        for item in task_results:
            if item.get("status") != NodeStatus.COMPLETED.value:
                err = item.get("error")
                if err:
                    parts.append(f"[failed] {item.get('objective')}: {err}")
                continue
            result = item.get("result") or {}
            text = (
                result.get("reply")
                or result.get("message")
                or result.get("summary")
                or ""
            )
            if not text:
                continue
            text = str(text).strip()
            # de-dupe exact repeats
            key = text[:200]
            if key in seen:
                continue
            seen.add(key)
            if len(task_results) > 1:
                parts.append(f"• {item.get('objective')}\n{text}")
            else:
                parts.append(text)

        if not parts:
            if graph.failed():
                errors = [r.get("error") for r in task_results if r.get("error")]
                reply = "Plan failed: " + "; ".join(str(e) for e in errors if e)
            else:
                reply = "Plan completed with no textual results."
        elif len(parts) == 1:
            reply = parts[0]
        else:
            header = graph.summary or "Plan results"
            reply = f"{header}\n\n" + "\n\n".join(parts)

        return {
            "ok": graph.succeeded(),
            "reply": reply,
            "plan_id": graph.plan_id,
            "summary": graph.summary,
            "task_count": len(graph.nodes),
            "results": task_results,
            "failed": graph.failed(),
        }


# ── Executor ──────────────────────────────────────────────────────

# Signature: (objective, preferred_agent, required_capabilities, parent_task_id) -> result dict
RunTaskFn = Callable[..., Dict[str, Any]]


@dataclass
class Executor:
    """
    Walk the graph: while incomplete, pick one ready task, run it, unlock deps.
    """

    run_task: RunTaskFn
    events: Optional[EventBus] = None
    aggregator: ResultAggregator = field(default_factory=ResultAggregator)
    max_steps: int = 50  # safety cap

    def execute(self, graph: TaskGraph) -> Dict[str, Any]:
        self._emit(EventType.PLAN_CREATED, {
            "plan_id": graph.plan_id,
            "summary": graph.summary,
            "node_count": len(graph.nodes),
        })

        steps = 0
        while not graph.completed() and steps < self.max_steps:
            steps += 1
            ready = graph.ready_tasks()
            if not ready:
                # deadlock or all remaining failed upstream
                for n in graph.nodes.values():
                    if n.status in (NodeStatus.PENDING, NodeStatus.READY):
                        graph.mark_failed(n.id, "Blocked: dependencies never completed")
                break

            # Deterministic: sort by id, take first
            ready.sort(key=lambda n: n.id)
            node = ready[0]

            self._emit(EventType.TASK_QUEUED, {
                "plan_id": graph.plan_id,
                "node_id": node.id,
                "objective": node.objective,
            })

            # Notify deps satisfied
            for dep in node.dependencies:
                self._emit(EventType.TASK_DEPENDENCY_SATISFIED, {
                    "plan_id": graph.plan_id,
                    "node_id": node.id,
                    "dependency": dep,
                })

            graph.mark_running(node.id)
            self._emit(EventType.TASK_EXECUTION_STARTED, {
                "plan_id": graph.plan_id,
                "node_id": node.id,
                "agent": node.assigned_agent,
                "objective": node.objective,
            })

            try:
                result = self.run_task(
                    objective=node.objective,
                    preferred_agent=node.assigned_agent,
                    required_capabilities=node.required_capabilities or None,
                    parent_task_id=node.parent,
                    node_id=node.id,
                    plan_id=graph.plan_id,
                )
                if not isinstance(result, dict):
                    result = {"ok": True, "reply": str(result)}
                if result.get("ok", True):
                    graph.mark_completed(node.id, result)
                    # Prefer agent assignment from result
                    if result.get("agent") and not node.assigned_agent:
                        node.assigned_agent = result["agent"]
                else:
                    graph.mark_failed(node.id, result.get("error") or "task failed")
            except Exception as e:
                graph.mark_failed(node.id, str(e))
                result = {"ok": False, "error": str(e)}

            self._emit(EventType.TASK_EXECUTION_FINISHED, {
                "plan_id": graph.plan_id,
                "node_id": node.id,
                "ok": node.status == NodeStatus.COMPLETED,
                "status": node.status.value,
            })

        graph.completed_at = time.time()
        aggregated = self.aggregator.aggregate(graph)

        self._emit(EventType.PLAN_COMPLETED, {
            "plan_id": graph.plan_id,
            "ok": aggregated.get("ok"),
            "task_count": len(graph.nodes),
            "failed": graph.failed(),
        })

        aggregated["graph"] = graph.to_dict()
        return aggregated

    def _emit(self, event_type: EventType, payload: Dict[str, Any]) -> None:
        if self.events:
            self.events.emit(event_type, payload, source="executor")
