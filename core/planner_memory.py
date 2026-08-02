"""
Planner memory – routing decisions + plan execution statistics.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class DecisionRecord:
    objective: str
    chosen_agent: str
    scores: Dict[str, float]
    success: Optional[bool] = None
    task_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlanRecord:
    plan_id: str
    summary: str
    objective: str
    task_count: int
    success: bool
    duration_s: float
    reasoning: str = ""
    single_step: bool = True
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class PlannerMemory:
    def __init__(self, max_history: int = 200):
        self.decisions: List[DecisionRecord] = []
        self.plans: List[PlanRecord] = []
        self.max_history = max_history
        self.stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "fail": 0})
        self.capability_prefs: Dict[str, str] = {}
        # plan-level counters
        self.plan_success = 0
        self.plan_fail = 0
        self.total_tasks_executed = 0
        self.total_plan_duration_s = 0.0

    # ── routing decisions (v0.1) ──────────────────────────────────

    def record_decision(
        self,
        objective: str,
        chosen_agent: str,
        scores: Dict[str, float],
        task_id: str = "",
    ) -> DecisionRecord:
        rec = DecisionRecord(
            objective=objective,
            chosen_agent=chosen_agent,
            scores=scores,
            task_id=task_id,
        )
        self.decisions.append(rec)
        if len(self.decisions) > self.max_history:
            self.decisions = self.decisions[-self.max_history:]
        return rec

    def mark_outcome(self, task_id: str, success: bool) -> None:
        for rec in reversed(self.decisions):
            if rec.task_id == task_id:
                rec.success = success
                key = "success" if success else "fail"
                self.stats[rec.chosen_agent][key] += 1
                break

    def success_rate(self, agent_name: str) -> float:
        s = self.stats[agent_name]
        total = s["success"] + s["fail"]
        if total == 0:
            return 0.5
        return s["success"] / total

    def bias_for(self, agent_name: str) -> float:
        rate = self.success_rate(agent_name)
        return (rate - 0.5) * 0.3

    def preferred_for_capability(self, capability: str) -> Optional[str]:
        return self.capability_prefs.get(capability)

    def set_preference(self, capability: str, agent_name: str) -> None:
        self.capability_prefs[capability] = agent_name

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.decisions[-limit:]]

    # ── plan records (v0.22) ──────────────────────────────────────

    def record_plan(
        self,
        plan_id: str,
        summary: str,
        objective: str,
        task_count: int,
        success: bool,
        duration_s: float,
        reasoning: str = "",
        single_step: bool = True,
    ) -> PlanRecord:
        rec = PlanRecord(
            plan_id=plan_id,
            summary=summary,
            objective=objective,
            task_count=task_count,
            success=success,
            duration_s=duration_s,
            reasoning=reasoning,
            single_step=single_step,
        )
        self.plans.append(rec)
        if len(self.plans) > self.max_history:
            self.plans = self.plans[-self.max_history:]
        if success:
            self.plan_success += 1
        else:
            self.plan_fail += 1
        self.total_tasks_executed += task_count
        self.total_plan_duration_s += duration_s
        return rec

    def recent_plans(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self.plans[-limit:]]

    def summary(self) -> Dict[str, Any]:
        n_plans = self.plan_success + self.plan_fail
        avg_tasks = (
            self.total_tasks_executed / n_plans if n_plans else 0.0
        )
        avg_duration = (
            self.total_plan_duration_s / n_plans if n_plans else 0.0
        )
        return {
            "total_decisions": len(self.decisions),
            "stats": dict(self.stats),
            "capability_prefs": dict(self.capability_prefs),
            "plans": {
                "total": n_plans,
                "success": self.plan_success,
                "fail": self.plan_fail,
                "avg_task_count": round(avg_tasks, 2),
                "avg_duration_s": round(avg_duration, 3),
            },
        }
