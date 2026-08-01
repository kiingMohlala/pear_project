"""
First-class Task object.

Every piece of work an agent does is a Task. This unlocks:
- background / async execution later
- retries
- multi-agent hand-offs
- audit trail
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Task:
    objective: str
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:10]}")
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None          # for sub-tasks later
    required_capabilities: List[str] = field(default_factory=list)

    # ── lifecycle helpers ─────────────────────────────────────────

    def assign(self, agent_name: str) -> None:
        self.assigned_agent = agent_name
        self.status = TaskStatus.ASSIGNED

    def start(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = time.time()

    def complete(self, result: Dict[str, Any]) -> None:
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()
        self.result = result

    def fail(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error

    def cancel(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.completed_at = time.time()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["priority"] = self.priority.value
        d["status"] = self.status.value
        return d

    def __repr__(self) -> str:
        return (
            f"<Task {self.id} status={self.status.value} "
            f"agent={self.assigned_agent!r} objective={self.objective[:40]!r}>"
        )
