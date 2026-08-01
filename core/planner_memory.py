"""
Planner memory – learns from past routing decisions.

Tracks:
  - which agent was chosen for similar objectives
  - success / failure counts
  - preferred agents per capability
  - recent execution history
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
    success: Optional[bool] = None   # filled when task finishes
    task_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class PlannerMemory:
    def __init__(self, max_history: int = 200):
        self.decisions: List[DecisionRecord] = []
        self.max_history = max_history
        # agent_name → {"success": n, "fail": n}
        self.stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "fail": 0})
        # capability → preferred agent (most successful)
        self.capability_prefs: Dict[str, str] = {}

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
            return 0.5  # neutral prior
        return s["success"] / total

    def bias_for(self, agent_name: str) -> float:
        """
        Small score adjustment based on historical success.
        Range roughly -0.1 … +0.15
        """
        rate = self.success_rate(agent_name)
        return (rate - 0.5) * 0.3

    def preferred_for_capability(self, capability: str) -> Optional[str]:
        return self.capability_prefs.get(capability)

    def set_preference(self, capability: str, agent_name: str) -> None:
        self.capability_prefs[capability] = agent_name

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [d.to_dict() for d in self.decisions[-limit:]]

    def summary(self) -> Dict[str, Any]:
        return {
            "total_decisions": len(self.decisions),
            "stats": dict(self.stats),
            "capability_prefs": dict(self.capability_prefs),
        }
