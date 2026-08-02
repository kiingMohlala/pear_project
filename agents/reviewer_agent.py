"""Reviewer & Critic agents for collaboration loops (v1.90)."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .base import Agent
from core.task import Task
from core.collaboration import heuristic_review, parse_review
from core.llm import BaseLLM, create_llm, EchoLLM


class ReviewerAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="reviewer",
            description=(
                "Reviews answers from other agents for completeness, relevance, "
                "and quality. Returns SCORE / FEEDBACK / ISSUES."
            ),
            capabilities=["review", "critique", "quality"],
            allowed_tools=[],
            system_prompt=(
                "You are PEAR's Reviewer. Score answers 0-1, list concrete issues, "
                "and give actionable feedback. Format: SCORE: x\\nFEEDBACK: ...\\nISSUES: a, b"
            ),
            **kwargs,
        )
        self.llm = llm or create_llm()
        self.permissions.grant("chat")

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        if any(k in obj for k in ("review", "critique", "score the answer", "quality check")):
            score = max(score, 0.9)
        return score

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        text = task.objective
        # extract objective/answer if structured
        obj_m = re.search(r"Objective:\s*(.+?)(?:\n\nAnswer:|$)", text, re.S | re.I)
        ans_m = re.search(r"Answer:\s*(.+)$", text, re.S | re.I)
        objective = (obj_m.group(1).strip() if obj_m else text)[:500]
        answer = ans_m.group(1).strip() if ans_m else text

        if self._llm_usable():
            try:
                resp = self.llm.chat(
                    self.system_prompt,
                    f"Objective: {objective}\n\nAnswer:\n{answer}\n\n"
                    "Respond with SCORE: <0-1>\nFEEDBACK: <text>\nISSUES: <comma list>",
                )
                body = (resp.content or "").strip()
                review = parse_review(body, answer)
                return {
                    "ok": True,
                    "reply": (
                        f"SCORE: {review.score}\nFEEDBACK: {review.feedback}\n"
                        f"ISSUES: {', '.join(review.issues) or 'none'}"
                    ),
                    "action": "review",
                    "review": review.to_dict(),
                }
            except Exception:
                pass
        review = heuristic_review(objective, answer)
        return {
            "ok": True,
            "reply": (
                f"SCORE: {review.score}\nFEEDBACK: {review.feedback}\n"
                f"ISSUES: {', '.join(review.issues) or 'none'}"
            ),
            "action": "review",
            "review": review.to_dict(),
        }

    def _llm_usable(self) -> bool:
        return not isinstance(self.llm, EchoLLM) and getattr(self.llm, "provider", "") not in ("echo", "")


class CriticAgent(ReviewerAgent):
    """Stricter reviewer variant."""

    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(llm=llm, **kwargs)
        self.name = "critic"
        self.description = (
            "Harsh critic that emphasizes risks, gaps, and unsupported claims."
        )
        self.capabilities = ["critique", "review", "risk"]
        self.system_prompt = (
            "You are PEAR's Critic. Be strict. Penalize vague or unsupported claims. "
            "Format: SCORE: x\\nFEEDBACK: ...\\nISSUES: ..."
        )

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        result = super()._process(task, **kwargs)
        # stricter: shave score slightly
        if result.get("review"):
            result["review"]["score"] = round(max(0.0, result["review"]["score"] - 0.05), 3)
            result["reply"] = (
                f"SCORE: {result['review']['score']}\n"
                f"FEEDBACK: {result['review'].get('feedback')}\n"
                f"ISSUES: {', '.join(result['review'].get('issues') or []) or 'none'}"
            )
        return result
