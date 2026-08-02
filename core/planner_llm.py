"""
LLM-assisted planner – produces structured ExecutionPlans.

Uses BaseLLM only (provider-agnostic). Falls back to a single-task
heuristic plan when the LLM is unavailable or returns invalid JSON.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .llm import BaseLLM, LLMMessage, create_llm
from .task_graph import TaskGraph, TaskNode

if TYPE_CHECKING:
    pass


@dataclass
class PlannedTask:
    objective: str
    required_capabilities: List[str] = field(default_factory=list)
    preferred_agent: Optional[str] = None
    depends_on: List[int] = field(default_factory=list)  # indices into the same task list
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionPlan:
    summary: str
    tasks: List[PlannedTask] = field(default_factory=list)
    reasoning: str = ""
    plan_id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:10]}")
    single_step: bool = False  # True when we kept the simple path
    created_at: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "summary": self.summary,
            "reasoning": self.reasoning,
            "single_step": self.single_step,
            "created_at": self.created_at,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    def to_graph(self) -> TaskGraph:
        """Materialise this plan as a TaskGraph (DAG)."""
        graph = TaskGraph(plan_id=self.plan_id, summary=self.summary)
        graph.metadata["reasoning"] = self.reasoning
        graph.metadata["single_step"] = self.single_step

        index_to_id: Dict[int, str] = {}
        for i, pt in enumerate(self.tasks):
            node = TaskNode(
                objective=pt.objective,
                assigned_agent=pt.preferred_agent,
                required_capabilities=list(pt.required_capabilities),
                metadata=dict(pt.metadata),
            )
            graph.add_node(node)
            index_to_id[i] = node.id

        for i, pt in enumerate(self.tasks):
            node_id = index_to_id[i]
            for dep_idx in pt.depends_on:
                if dep_idx in index_to_id and dep_idx != i:
                    try:
                        graph.add_dependency(node_id, index_to_id[dep_idx])
                    except Exception:
                        pass  # skip invalid / cyclic deps from LLM noise

        return graph


PLANNER_SYSTEM = """You are PEAR's task planner. Given a user objective and a list of agents
with their capabilities, produce a structured execution plan.

Rules:
- Prefer the minimum number of tasks that still solve the objective.
- Simple requests (chat, notes, single desktop action) → exactly ONE task.
- Complex requests (multi-step analysis, document review + summary, etc.) → ordered steps.
- Each task must list required_capabilities from the agents' capabilities.
- preferred_agent must be one of the agent names provided, or null.
- depends_on is a list of zero-based indices of prior tasks in the same list.
- Never invent agents. Never return executable code. Never use markdown fences.
- Respond with ONLY valid JSON matching this schema:

{
  "summary": "short plan title",
  "reasoning": "one or two sentences explaining the plan",
  "tasks": [
    {
      "objective": "what this step should do",
      "required_capabilities": ["capability"],
      "preferred_agent": "agent_name_or_null",
      "depends_on": []
    }
  ]
}
"""


class PlannerLLM:
    def __init__(self, llm: Optional[BaseLLM] = None):
        self.llm = llm or create_llm()

    def plan(
        self,
        objective: str,
        agents: List[Dict[str, Any]],
        *,
        force_single: bool = False,
    ) -> ExecutionPlan:
        """
        Produce an ExecutionPlan for the objective given agent catalog entries.
        Each agent dict: {name, description, capabilities, allowed_tools}.
        """
        objective = (objective or "").strip()
        if not objective:
            return ExecutionPlan(summary="empty", tasks=[], reasoning="Empty objective", single_step=True)

        if force_single or self._looks_simple(objective, agents):
            return self._single_task_plan(objective, agents)

        if not self.llm.is_available() or getattr(self.llm, "provider", "") == "echo":
            return self._heuristic_plan(objective, agents)

        try:
            return self._llm_plan(objective, agents)
        except Exception as e:
            plan = self._heuristic_plan(objective, agents)
            plan.reasoning = f"LLM plan failed ({e}); used heuristic. " + plan.reasoning
            return plan

    # ── paths ─────────────────────────────────────────────────────

    def _llm_plan(self, objective: str, agents: List[Dict[str, Any]]) -> ExecutionPlan:
        catalog = []
        for a in agents:
            catalog.append({
                "name": a.get("name"),
                "description": a.get("description", ""),
                "capabilities": a.get("capabilities", []),
            })

        user_prompt = (
            f"User objective:\n{objective}\n\n"
            f"Available agents (JSON):\n{json.dumps(catalog, indent=2)}\n\n"
            "Return the execution plan JSON now."
        )

        response = self.llm.chat(
            system=PLANNER_SYSTEM,
            user=user_prompt,
            temperature=0.2,
            max_tokens=1024,
        )
        data = self._parse_json(response.content)
        return self._from_dict(data, objective, agents, raw={"llm": response.content})

    def _heuristic_plan(self, objective: str, agents: List[Dict[str, Any]]) -> ExecutionPlan:
        """Keyword-based multi-step fallback when LLM is offline."""
        lower = objective.lower()
        tasks: List[PlannedTask] = []

        # Document / legal multi-step
        legal_signals = ["nda", "contract", "clause", "legal", "liability", "terms of service"]
        if any(s in lower for s in legal_signals) and any(
            "legal" in (a.get("capabilities") or []) or a.get("name") == "legal" for a in agents
        ):
            legal = self._agent_for_caps(agents, ["legal", "document_review"])
            tasks = [
                PlannedTask(
                    objective=f"Locate and read relevant document for: {objective}",
                    required_capabilities=["legal", "document_review", "file_reading"],
                    preferred_agent=legal or self._agent_for_caps(agents, ["file_reading", "chat"]),
                ),
                PlannedTask(
                    objective=f"Extract key clauses and obligations related to: {objective}",
                    required_capabilities=["legal", "document_review", "clause_extraction"],
                    preferred_agent=legal,
                    depends_on=[0],
                ),
                PlannedTask(
                    objective=f"Analyse risks and produce executive summary for: {objective}",
                    required_capabilities=["legal", "risk_analysis"],
                    preferred_agent=legal,
                    depends_on=[1],
                ),
            ]
            return ExecutionPlan(
                summary="Document review pipeline",
                tasks=tasks,
                reasoning="Heuristic multi-step legal/document plan",
                single_step=False,
            )

        # Finance multi-step
        finance_signals = ["budget", "invoice", "expense", "cashflow", "bank statement"]
        if any(s in lower for s in finance_signals):
            tasks = [
                PlannedTask(
                    objective=f"Gather relevant financial context for: {objective}",
                    required_capabilities=["finance", "chat"],
                    preferred_agent=self._agent_for_caps(agents, ["finance"]),
                ),
                PlannedTask(
                    objective=f"Analyse and summarize: {objective}",
                    required_capabilities=["finance", "analysis"],
                    preferred_agent=self._agent_for_caps(agents, ["finance", "analysis"]),
                    depends_on=[0],
                ),
            ]
            return ExecutionPlan(
                summary="Finance analysis pipeline",
                tasks=tasks,
                reasoning="Heuristic multi-step finance plan",
                single_step=False,
            )

        return self._single_task_plan(objective, agents, reasoning="Heuristic single-step")

    def _single_task_plan(
        self,
        objective: str,
        agents: List[Dict[str, Any]],
        reasoning: str = "Simple request → single task",
    ) -> ExecutionPlan:
        agent_name = self._best_agent(objective, agents)
        caps: List[str] = []
        for a in agents:
            if a.get("name") == agent_name:
                caps = list(a.get("capabilities") or [])
                break
        return ExecutionPlan(
            summary=objective[:80],
            tasks=[
                PlannedTask(
                    objective=objective,
                    required_capabilities=caps[:3],
                    preferred_agent=agent_name,
                )
            ],
            reasoning=reasoning,
            single_step=True,
        )

    # ── helpers ───────────────────────────────────────────────────

    def _looks_simple(self, objective: str, agents: List[Dict[str, Any]]) -> bool:
        lower = objective.lower().strip()
        if lower.startswith("note:") or lower.startswith("remember:"):
            return True
        if lower in ("list notes", "show notes", "my notes", "hello", "hi", "hey"):
            return True
        # Short chat-like messages
        if len(lower.split()) <= 4 and not any(
            s in lower for s in ("review", "analyse", "analyze", "summarize", "compare", "and then")
        ):
            return True
        return False

    def _best_agent(self, objective: str, agents: List[Dict[str, Any]]) -> Optional[str]:
        lower = objective.lower()
        best_name = None
        best_score = -1.0
        for a in agents:
            score = 0.0
            for cap in a.get("capabilities") or []:
                if cap.replace("_", " ") in lower or cap in lower:
                    score += 0.4
            for token in (a.get("description") or "").lower().split():
                if len(token) > 3 and token in lower:
                    score += 0.1
            if score > best_score:
                best_score = score
                best_name = a.get("name")
        if best_score <= 0 and agents:
            # default to personal / first
            for a in agents:
                if a.get("name") == "personal":
                    return "personal"
            return agents[0].get("name")
        return best_name

    def _agent_for_caps(self, agents: List[Dict[str, Any]], caps: List[str]) -> Optional[str]:
        for a in agents:
            acaps = set(a.get("capabilities") or [])
            if acaps & set(caps):
                return a.get("name")
        return self._best_agent(" ".join(caps), agents)

    def _parse_json(self, text: str) -> dict:
        text = (text or "").strip()
        # strip markdown fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # find outermost object
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    def _from_dict(
        self,
        data: dict,
        objective: str,
        agents: List[Dict[str, Any]],
        raw: Optional[dict] = None,
    ) -> ExecutionPlan:
        agent_names = {a.get("name") for a in agents}
        tasks_raw = data.get("tasks") or []
        if not tasks_raw:
            return self._single_task_plan(objective, agents, reasoning="LLM returned no tasks")

        planned: List[PlannedTask] = []
        for t in tasks_raw:
            if not isinstance(t, dict):
                continue
            pref = t.get("preferred_agent")
            if pref and pref not in agent_names:
                pref = self._agent_for_caps(agents, t.get("required_capabilities") or [])
            depends = t.get("depends_on") or []
            depends = [int(d) for d in depends if str(d).isdigit() or isinstance(d, int)]
            planned.append(
                PlannedTask(
                    objective=str(t.get("objective") or objective),
                    required_capabilities=list(t.get("required_capabilities") or []),
                    preferred_agent=pref,
                    depends_on=depends,
                    metadata=dict(t.get("metadata") or {}),
                )
            )

        if not planned:
            return self._single_task_plan(objective, agents)

        return ExecutionPlan(
            summary=str(data.get("summary") or objective[:80]),
            tasks=planned,
            reasoning=str(data.get("reasoning") or ""),
            single_step=len(planned) == 1,
            raw=raw or data,
        )
