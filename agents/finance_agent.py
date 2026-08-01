"""
Finance agent – placeholder for v0.4.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import Agent
from core.task import Task


class FinanceAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(
            name="finance",
            description=(
                "Analyses bank statements, budgets, invoices, and financial "
                "spreadsheets. Not active until v0.4."
            ),
            capabilities=["finance", "analysis", "budget", "invoice"],
            allowed_tools=["read_document", "summarize_text"],
            system_prompt="You are PEAR's Finance Agent. You will analyse statements and budgets.",
            **kwargs,
        )

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        finance_signals = ["budget", "invoice", "bank statement", "expense", "finance", "cashflow"]
        if any(s in obj for s in finance_signals):
            score = max(score, 0.7)
        return score

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        # Example of the correct pattern for needing another agent:
        # NEVER call LegalAgent directly – ask the planner.
        # result = self.request_subtask("review contract clauses", required_capabilities=["legal"])
        return {
            "ok": True,
            "reply": (
                "Finance analysis is scheduled for v0.4. "
                "The agent slot and capability matching are ready. "
                f"You asked: “{task.objective}”"
            ),
            "action": "placeholder",
        }
