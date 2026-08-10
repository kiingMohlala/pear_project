"""
Autonomous Goal Execution (v2.00).

Goals persist across restarts, expand task graphs, wait on external conditions,
and replan when steps fail.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class GoalStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class WaitReason(str, Enum):
    USER_APPROVAL = "user_approval"
    SCHEDULED = "scheduled"
    EXTERNAL_EVENT = "external_event"
    CONNECTOR = "connector"
    DEPENDENCY = "dependency"
    NONE = "none"


@dataclass
class Milestone:
    id: str
    title: str
    done: bool = False
    completed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Milestone":
        return cls(
            id=d["id"],
            title=d["title"],
            done=bool(d.get("done")),
            completed_at=d.get("completed_at"),
        )


@dataclass
class GoalStep:
    id: str
    objective: str
    status: str = "pending"  # pending|running|done|failed|skipped
    agent: str = ""
    result: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    depends_on: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GoalStep":
        return cls(
            id=d["id"],
            objective=d["objective"],
            status=d.get("status", "pending"),
            agent=d.get("agent") or "",
            result=d.get("result"),
            error=d.get("error"),
            attempts=int(d.get("attempts") or 0),
            depends_on=list(d.get("depends_on") or []),
        )


@dataclass
class Goal:
    id: str
    title: str
    objective: str
    user_id: Optional[str] = None  # PEAR 3.1 Gate 4: owning authenticated identity
    status: GoalStatus = GoalStatus.PENDING
    priority: int = 5
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    steps: List[GoalStep] = field(default_factory=list)
    milestones: List[Milestone] = field(default_factory=list)
    progress: float = 0.0  # 0-1
    wait_reason: WaitReason = WaitReason.NONE
    wait_until: Optional[float] = None
    wait_note: str = ""
    parent_goal_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None
    replan_count: int = 0
    max_replans: int = 3
    max_step_attempts: int = 2

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "user_id": self.user_id,
            "status": self.status.value if isinstance(self.status, GoalStatus) else self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "steps": [s.to_dict() for s in self.steps],
            "milestones": [m.to_dict() for m in self.milestones],
            "progress": self.progress,
            "wait_reason": self.wait_reason.value if isinstance(self.wait_reason, WaitReason) else self.wait_reason,
            "wait_until": self.wait_until,
            "wait_note": self.wait_note,
            "parent_goal_id": self.parent_goal_id,
            "metadata": self.metadata,
            "last_error": self.last_error,
            "replan_count": self.replan_count,
            "max_replans": self.max_replans,
            "max_step_attempts": self.max_step_attempts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        return cls(
            id=d["id"],
            title=d.get("title") or d.get("objective", "")[:60],
            objective=d["objective"],
            user_id=d.get("user_id"),
            status=GoalStatus(d.get("status", "pending")),
            priority=int(d.get("priority") or 5),
            created_at=float(d.get("created_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            steps=[GoalStep.from_dict(s) for s in d.get("steps") or []],
            milestones=[Milestone.from_dict(m) for m in d.get("milestones") or []],
            progress=float(d.get("progress") or 0.0),
            wait_reason=WaitReason(d.get("wait_reason") or "none"),
            wait_until=d.get("wait_until"),
            wait_note=d.get("wait_note") or "",
            parent_goal_id=d.get("parent_goal_id"),
            metadata=dict(d.get("metadata") or {}),
            last_error=d.get("last_error"),
            replan_count=int(d.get("replan_count") or 0),
            max_replans=int(d.get("max_replans") or 3),
            max_step_attempts=int(d.get("max_step_attempts") or 2),
        )

    def recompute_progress(self) -> float:
        if not self.steps:
            self.progress = 1.0 if self.status == GoalStatus.COMPLETED else 0.0
            return self.progress
        done = sum(1 for s in self.steps if s.status in ("done", "skipped"))
        self.progress = round(done / len(self.steps), 3)
        # milestones
        if self.milestones:
            for i, m in enumerate(self.milestones):
                threshold = (i + 1) / len(self.milestones)
                if self.progress >= threshold and not m.done:
                    m.done = True
                    m.completed_at = time.time()
        return self.progress


class GoalManager:
    def __init__(self, orchestrator: "Orchestrator", persist_dir: Optional[Path] = None):
        self.orch = orchestrator
        if persist_dir is None:
            base = getattr(getattr(orchestrator, "memory", None), "persist_dir", None)
            persist_dir = Path(base) / "goals" if base else Path.home() / ".pear" / "goals"
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.goals: Dict[str, Goal] = {}
        self._load_all()

    # ── persistence ───────────────────────────────────────────────

    def _path(self, goal_id: str) -> Path:
        return self.persist_dir / f"{goal_id}.json"

    def _save(self, goal: Goal) -> None:
        goal.updated_at = time.time()
        self._path(goal.id).write_text(json.dumps(goal.to_dict(), indent=2), encoding="utf-8")

    def _load_all(self) -> None:
        for p in self.persist_dir.glob("goal_*.json"):
            try:
                g = Goal.from_dict(json.loads(p.read_text(encoding="utf-8")))
                self.goals[g.id] = g
            except Exception:
                continue

    # ── events / tracing ──────────────────────────────────────────

    def _span(self, name: str, **attrs):
        try:
            from .tracing import get_tracer
            return get_tracer().span(name, kind="goal", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _emit(self, kind: str, goal: Goal, **extra):
        try:
            from .events import EventType
            self.orch.events.emit(
                EventType.NOTE,
                {"kind": kind, "goal_id": goal.id, "status": goal.status.value, **extra},
                source="goals",
            )
        except Exception:
            pass

    # ── lifecycle ─────────────────────────────────────────────────

    def create(
        self,
        objective: str,
        *,
        title: Optional[str] = None,
        priority: int = 5,
        milestones: Optional[List[str]] = None,
        auto_start: bool = True,
    ) -> Goal:
        gid = f"goal_{uuid.uuid4().hex[:10]}"
        goal = Goal(
            id=gid,
            title=(title or objective[:60]).strip(),
            objective=objective.strip(),
            user_id=getattr(self.orch, "user_id", None),
            priority=priority,
            milestones=[
                Milestone(id=f"ms_{i}", title=t)
                for i, t in enumerate(milestones or ["Planned", "Executing", "Done"])
            ],
        )
        self.goals[gid] = goal
        self._save(goal)
        self._emit("goal_created", goal)
        if auto_start:
            self.plan(gid)
            self.start(gid)
        return goal

    def get(self, goal_id: str) -> Goal:
        if goal_id not in self.goals:
            path = self._path(goal_id)
            if path.exists():
                self.goals[goal_id] = Goal.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if goal_id not in self.goals:
            raise KeyError(f"Unknown goal: {goal_id}")
        return self.goals[goal_id]

    def list_goals(self, status: Optional[str] = None) -> List[Goal]:
        items = list(self.goals.values())
        if status:
            items = [g for g in items if g.status.value == status]
        items.sort(key=lambda g: (-g.priority, -g.created_at))
        return items

    def plan(self, goal_id: str) -> Goal:
        goal = self.get(goal_id)
        with self._span("goal.plan", goal_id=goal_id):
            goal.status = GoalStatus.PLANNING
            self._emit("goal_planning", goal)
            steps = self._decompose(goal.objective)
            goal.steps = steps
            if goal.milestones and not goal.milestones[0].done:
                goal.milestones[0].done = True
                goal.milestones[0].completed_at = time.time()
            goal.recompute_progress()
            self._save(goal)
        return goal

    def _decompose(self, objective: str) -> List[GoalStep]:
        """Heuristic decomposition; uses planner when available for richer graphs."""
        obj = objective.strip()
        # try orchestrator planner for multi-step
        try:
            if hasattr(self.orch, "plan"):
                task = self.orch.plan(obj)
                # single task → expand heuristically for long goals
        except Exception:
            pass

        parts: List[str] = []
        # split on conjunctions / semicolons
        chunks = [c.strip() for c in re_split_objective(obj) if c.strip()]
        if len(chunks) == 1:
            # generic research/execute/verify pipeline for complex goals
            if any(k in obj.lower() for k in ("research", "analyze", "plan", "build", "implement")):
                parts = [
                    f"Clarify scope and success criteria for: {obj}",
                    f"Gather information relevant to: {obj}",
                    f"Execute primary work for: {obj}",
                    f"Verify results and summarize: {obj}",
                ]
            else:
                parts = [obj]
        else:
            parts = chunks

        steps = []
        prev = None
        for i, p in enumerate(parts):
            sid = f"step_{i+1}"
            deps = [prev] if prev else []
            steps.append(GoalStep(id=sid, objective=p, depends_on=deps))
            prev = sid
        return steps

    def start(self, goal_id: str) -> Goal:
        goal = self.get(goal_id)
        if goal.status in (GoalStatus.COMPLETED, GoalStatus.CANCELLED):
            return goal
        with self._span("goal.start", goal_id=goal_id):
            if not goal.steps:
                self.plan(goal_id)
                goal = self.get(goal_id)
            goal.status = GoalStatus.RUNNING
            goal.started_at = goal.started_at or time.time()
            self._emit("goal_started", goal)
            self._save(goal)
            self._tick(goal)
        return goal

    def pause(self, goal_id: str) -> Goal:
        goal = self.get(goal_id)
        goal.status = GoalStatus.PAUSED
        self._emit("goal_paused", goal)
        self._save(goal)
        return goal

    def resume(self, goal_id: str) -> Goal:
        goal = self.get(goal_id)
        if goal.status in (GoalStatus.PAUSED, GoalStatus.WAITING, GoalStatus.BLOCKED):
            goal.status = GoalStatus.RUNNING
            goal.wait_reason = WaitReason.NONE
            goal.wait_note = ""
            self._emit("goal_resumed", goal)
            self._save(goal)
            self._tick(goal)
        return goal

    def cancel(self, goal_id: str) -> Goal:
        goal = self.get(goal_id)
        goal.status = GoalStatus.CANCELLED
        goal.completed_at = time.time()
        self._emit("goal_cancelled", goal)
        self._save(goal)
        return goal

    def wait(
        self,
        goal_id: str,
        reason: WaitReason,
        *,
        until: Optional[float] = None,
        note: str = "",
    ) -> Goal:
        goal = self.get(goal_id)
        goal.status = GoalStatus.WAITING
        goal.wait_reason = reason
        goal.wait_until = until
        goal.wait_note = note
        self._emit("goal_waiting", goal, reason=reason.value)
        self._save(goal)
        return goal

    def tick_all(self) -> List[str]:
        """Advance all running/waiting goals. Returns ids that progressed."""
        progressed = []
        now = time.time()
        for goal in list(self.goals.values()):
            if goal.status == GoalStatus.WAITING and goal.wait_until and now >= goal.wait_until:
                goal.status = GoalStatus.RUNNING
                goal.wait_reason = WaitReason.NONE
                self._emit("goal_resumed", goal, auto=True)
                self._save(goal)
            if goal.status == GoalStatus.RUNNING:
                before = goal.progress
                self._tick(goal)
                if goal.progress != before or goal.status != GoalStatus.RUNNING:
                    progressed.append(goal.id)
        return progressed

    def _tick(self, goal: Goal) -> None:
        with self._span("goal.tick", goal_id=goal.id):
            if goal.status != GoalStatus.RUNNING:
                return
            # find ready steps
            done_ids = {s.id for s in goal.steps if s.status in ("done", "skipped")}
            ready = [
                s for s in goal.steps
                if s.status == "pending" and all(d in done_ids for d in s.depends_on)
            ]
            if not ready:
                if all(s.status in ("done", "skipped", "failed") for s in goal.steps):
                    if any(s.status == "failed" for s in goal.steps):
                        self._handle_failure(goal)
                    else:
                        goal.status = GoalStatus.COMPLETED
                        goal.completed_at = time.time()
                        goal.recompute_progress()
                        # mark final milestone
                        for m in goal.milestones:
                            m.done = True
                            m.completed_at = m.completed_at or time.time()
                        self._emit("goal_completed", goal)
                        self._save(goal)
                return

            # execute one ready step per tick (deterministic)
            step = ready[0]
            self._execute_step(goal, step)

    def _execute_step(self, goal: Goal, step: GoalStep) -> None:
        step.status = "running"
        step.attempts += 1
        self._save(goal)
        self._emit("goal_step_started", goal, step_id=step.id)
        t0 = time.time()
        try:
            # optional collaboration for complex steps
            use_collab = len(step.objective) > 100 and hasattr(self.orch, "collaboration")
            if use_collab:
                result = self.orch.collaboration.run(step.objective, mode="reviewer")
                ok = result.ok
                reply = result.reply
                agent = "collaboration"
            else:
                r = self.orch.route(step.objective)
                ok = bool(r.get("ok"))
                reply = r.get("reply") or r.get("error") or ""
                agent = r.get("agent") or ""
            step.agent = agent
            if ok:
                step.status = "done"
                step.result = str(reply)[:2000]
                step.error = None
            else:
                step.error = str(reply)[:500]
                if step.attempts < goal.max_step_attempts:
                    step.status = "pending"  # retry later
                else:
                    step.status = "failed"
        except Exception as e:
            step.error = str(e)
            if step.attempts < goal.max_step_attempts:
                step.status = "pending"
            else:
                step.status = "failed"
        goal.recompute_progress()
        self._emit(
            "goal_step_finished",
            goal,
            step_id=step.id,
            step_status=step.status,
            latency_ms=(time.time() - t0) * 1000,
        )
        self._save(goal)
        # continue if still running
        if goal.status == GoalStatus.RUNNING:
            if step.status == "failed":
                self._handle_failure(goal)
            else:
                self._tick(goal)

    def _handle_failure(self, goal: Goal) -> None:
        failed = [s for s in goal.steps if s.status == "failed"]
        goal.last_error = "; ".join(s.error or s.objective for s in failed[:3])
        if goal.replan_count < goal.max_replans:
            with self._span("goal.replan", goal_id=goal.id):
                goal.replan_count += 1
                self._emit("goal_replanning", goal, attempt=goal.replan_count)
                # adaptive: replace failed steps with recovery objectives
                new_steps = []
                for s in goal.steps:
                    if s.status == "failed":
                        recovery = GoalStep(
                            id=f"{s.id}_retry_{goal.replan_count}",
                            objective=f"Recover and complete: {s.objective}. Previous error: {s.error}",
                            depends_on=s.depends_on,
                        )
                        new_steps.append(recovery)
                    else:
                        new_steps.append(s)
                goal.steps = new_steps
                goal.status = GoalStatus.RUNNING
                goal.recompute_progress()
                self._save(goal)
                self._tick(goal)
        else:
            goal.status = GoalStatus.FAILED
            goal.completed_at = time.time()
            self._emit("goal_failed", goal)
            self._save(goal)

    def status_report(self, goal_id: str) -> str:
        g = self.get(goal_id)
        lines = [
            f"# Goal {g.id}",
            f"**{g.title}**",
            f"Status: {g.status.value} · Progress: {int(g.progress*100)}% · Replans: {g.replan_count}",
            f"Objective: {g.objective}",
            "",
            "## Milestones",
        ]
        for m in g.milestones:
            mark = "✓" if m.done else "·"
            lines.append(f"  {mark} {m.title}")
        lines.append("")
        lines.append("## Steps")
        for s in g.steps:
            lines.append(f"  [{s.status}] {s.id}: {s.objective[:80]}")
            if s.error:
                lines.append(f"      error: {s.error[:100]}")
        if g.wait_reason != WaitReason.NONE:
            lines.append(f"\nWaiting: {g.wait_reason.value} — {g.wait_note}")
        if g.last_error:
            lines.append(f"\nLast error: {g.last_error}")
        return "\n".join(lines)


def re_split_objective(obj: str) -> List[str]:
    import re
    # split on ; or " then " / " and then "
    parts = re.split(r"\s*;\s*|\s+then\s+|\s+and then\s+", obj, flags=re.I)
    return [p.strip() for p in parts if p.strip()]
