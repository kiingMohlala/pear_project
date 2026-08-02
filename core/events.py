"""
Simple event bus.

Everything interesting that happens in PEAR emits an event.
The dashboard (or any subscriber) can listen without coupling to agents.

Event flow example:
  TaskCreated → AgentSelected → TaskStarted → ToolCalled → ToolFinished → TaskCompleted
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    AGENT_SELECTED = "agent_selected"
    TASK_STARTED = "task_started"
    TOOL_CALLED = "tool_called"
    TOOL_FINISHED = "tool_finished"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    SUBTASK_REQUESTED = "subtask_requested"
    NOTE = "note"  # free-form log
    # v0.22 – intelligent planner
    PLAN_CREATED = "plan_created"
    TASK_QUEUED = "task_queued"
    TASK_DEPENDENCY_SATISFIED = "task_dependency_satisfied"
    TASK_EXECUTION_STARTED = "task_execution_started"
    TASK_EXECUTION_FINISHED = "task_execution_finished"
    PLAN_COMPLETED = "plan_completed"
    # v0.33 – background jobs
    JOB_CREATED = "job_created"
    JOB_QUEUED = "job_queued"
    JOB_STARTED = "job_started"
    JOB_PROGRESS = "job_progress"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"
    # v0.60 – workflows
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_STEP_STARTED = "workflow_step_started"
    WORKFLOW_STEP_COMPLETED = "workflow_step_completed"
    WORKFLOW_WAITING_APPROVAL = "workflow_waiting_approval"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_CANCELLED = "workflow_cancelled"


@dataclass
class Event:
    type: EventType
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # e.g. agent name or "planner"
    trace_id: Optional[str] = None
    span_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


Listener = Callable[[Event], None]


class EventBus:
    """
    In-process pub/sub.
    Thread-safety is not required for v0.1; add locks later if needed.
    """

    def __init__(self, keep_history: int = 500):
        self._listeners: Dict[Optional[EventType], List[Listener]] = {}
        self.history: List[Event] = []
        self.keep_history = keep_history

    def on(self, event_type: Optional[EventType], listener: Listener) -> None:
        """
        Subscribe. Pass event_type=None to receive every event.
        """
        self._listeners.setdefault(event_type, []).append(listener)

    def off(self, event_type: Optional[EventType], listener: Listener) -> None:
        if event_type in self._listeners:
            self._listeners[event_type] = [
                l for l in self._listeners[event_type] if l is not listener
            ]

    def emit(
        self,
        event_type: EventType,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "",
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
    ) -> Event:
        # Auto-attach active trace context when not provided
        if trace_id is None or span_id is None:
            try:
                from .tracing import get_tracer
                ctx = get_tracer().context_ids()
                trace_id = trace_id or ctx.get("trace_id")
                span_id = span_id or ctx.get("span_id")
            except Exception:
                pass
        event = Event(
            type=event_type,
            payload=payload or {},
            source=source,
            trace_id=trace_id,
            span_id=span_id,
        )
        self.history.append(event)
        if len(self.history) > self.keep_history:
            self.history = self.history[-self.keep_history:]

        # Specific listeners
        for listener in self._listeners.get(event_type, []):
            try:
                listener(event)
            except Exception:
                pass  # never let a listener break the pipeline

        # Wildcard listeners
        for listener in self._listeners.get(None, []):
            try:
                listener(event)
            except Exception:
                pass

        return event

    def recent(self, limit: int = 20, event_type: Optional[EventType] = None) -> List[Event]:
        items = self.history
        if event_type is not None:
            items = [e for e in items if e.type == event_type]
        return items[-limit:]
