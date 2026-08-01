"""
Legal agent – placeholder for v0.3 (document review).
"""

from __future__ import annotations

from typing import Any, Dict

from .base import Agent
from core.task import Task


class LegalAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(
            name="legal",
            description=(
                "Reviews contracts, NDAs, terms of service, and other legal "
                "documents. Not active until v0.3."
            ),
            capabilities=["legal", "document_review", "contract"],
            allowed_tools=["read_document", "summarize_text"],
            system_prompt="You are PEAR's Legal Agent. You will review contracts and legal documents.",
            **kwargs,
        )

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        legal_signals = ["contract", "nda", "legal", "terms of service", "clause", "liability"]
        if any(s in obj for s in legal_signals):
            score = max(score, 0.7)
        return score

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        return {
            "ok": True,
            "reply": (
                "Legal document review is planned for v0.3. "
                "I can’t analyse contracts yet, but the agent slot is ready."
            ),
            "action": "placeholder",
        }
