"""
Background jobs for PEAR (v0.33).

Jobs wrap an objective (and optional ExecutionPlan / TaskGraph snapshot)
so long-running work can leave the interactive session.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class JobPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


PRIORITY_ORDER = {
    JobPriority.CRITICAL: 0,
    JobPriority.HIGH: 1,
    JobPriority.NORMAL: 2,
    JobPriority.LOW: 3,
}


@dataclass
class Job:
    objective: str
    id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:10]}")
    status: JobStatus = JobStatus.PENDING
    priority: JobPriority = JobPriority.NORMAL
    progress: float = 0.0  # 0.0 – 1.0
    progress_message: str = ""
    created_at: float = field(default_factory=time.time)
    scheduled_at: Optional[float] = None  # when eligible to run (None = now)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    updated_at: float = field(default_factory=time.time)

    # Execution payload
    plan_snapshot: Optional[Dict[str, Any]] = None
    graph_snapshot: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    # Retry / schedule
    attempts: int = 0
    max_attempts: int = 3
    schedule: Optional[Dict[str, Any]] = None  # {kind, interval_s, cron, next_run}
    parent_job_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        d = dict(data)
        if "status" in d and not isinstance(d["status"], JobStatus):
            d["status"] = JobStatus(d["status"])
        if "priority" in d and not isinstance(d["priority"], JobPriority):
            d["priority"] = JobPriority(d["priority"])
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ScheduleSpec:
    """Simple schedule descriptor (cron-style deferred to later)."""

    kind: str = "once"  # once | interval | daily | weekly
    interval_s: Optional[float] = None
    hour: Optional[int] = None  # for daily
    weekday: Optional[int] = None  # 0=Mon for weekly
    next_run: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleSpec":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in data.items() if k in known})

    def compute_next(self, after: Optional[float] = None) -> Optional[float]:
        import datetime as dt

        now = after or time.time()
        if self.kind == "once":
            return self.next_run if self.next_run and self.next_run > now else None
        if self.kind == "interval":
            step = float(self.interval_s or 60)
            return now + step
        if self.kind == "daily":
            hour = int(self.hour if self.hour is not None else 9)
            local = dt.datetime.fromtimestamp(now)
            candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate.timestamp() <= now:
                candidate = candidate + dt.timedelta(days=1)
            return candidate.timestamp()
        if self.kind == "weekly":
            hour = int(self.hour if self.hour is not None else 9)
            target = int(self.weekday if self.weekday is not None else 0)
            local = dt.datetime.fromtimestamp(now)
            days_ahead = (target - local.weekday()) % 7
            candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            candidate = candidate + dt.timedelta(days=days_ahead)
            if candidate.timestamp() <= now:
                candidate = candidate + dt.timedelta(days=7)
            return candidate.timestamp()
        return None
