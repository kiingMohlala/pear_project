"""
Personal / general-purpose agent – chat, notes, file reading.
Uses the shared LLM abstraction + knowledge retrieval.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import Agent
from core.task import Task
from core.llm import BaseLLM, LLMMessage, create_llm

if TYPE_CHECKING:
    pass


class PersonalAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="personal",
            description=(
                "General personal assistant. Handles conversation, notes, "
                "and summarizing uploaded documents (PDF, DOCX)."
            ),
            capabilities=["chat", "notes", "file_reading"],
            allowed_tools=["summarize_text", "read_document"],
            system_prompt=(
                "You are PEAR, a helpful personal agent. "
                "You chat naturally, keep notes when asked, and answer questions "
                "about documents the user has uploaded. "
                "Be concise unless the user asks for detail. "
                "If you use retrieved document context, ground your answer in it."
            ),
            **kwargs,
        )
        self.llm: BaseLLM = llm or create_llm()

    def can_handle(self, task: Task) -> float:
        base = super().can_handle(task)
        return max(base, 0.2)

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        user_input = task.objective
        lower = user_input.lower().strip()

        # ── Structured local actions (no LLM needed) ─────────────
        if lower.startswith("note:") or lower.startswith("remember:"):
            body = user_input.split(":", 1)[1].strip()
            title = body[:40] + ("…" if len(body) > 40 else "")
            self.memory.add_note(title=title, body=body)
            return {
                "ok": True,
                "reply": f"Got it. I've saved a note: “{title}”",
                "action": "note_added",
            }

        if lower in ("list notes", "show notes", "my notes"):
            notes = self.memory.list_notes()
            if not notes:
                return {"ok": True, "reply": "You have no notes yet."}
            lines = [f"• [{n['id']}] {n['title']}" for n in notes]
            return {"ok": True, "reply": "Your notes:\n" + "\n".join(lines)}

        # ── LLM path with retrieval ──────────────────────────────
        return self._llm_reply(user_input)

    def _llm_reply(self, user_message: str) -> Dict[str, Any]:
        # Retrieve relevant knowledge
        knowledge_ctx = self.memory.knowledge.build_context(user_message, max_chars=6000)

        # Working memory → chat history (exclude the just-added user turn duplicate)
        history_msgs: List[LLMMessage] = []
        for msg in self.memory.working.get_history(limit=12):
            if msg.role in ("user", "assistant"):
                # skip the current user message if already appended by think()
                history_msgs.append(LLMMessage(role=msg.role, content=msg.content))

        # Drop last user message from history – generate() will add current user
        if history_msgs and history_msgs[-1].role == "user":
            history_msgs = history_msgs[:-1]

        system = self.system_prompt
        if knowledge_ctx:
            system = (
                f"{self.system_prompt}\n\n"
                f"## Retrieved knowledge\n{knowledge_ctx}\n\n"
                "Use the retrieved knowledge when relevant. "
                "If it does not answer the question, say so."
            )

        # Long-term prefs (light touch)
        prefs = self.memory.long_term.preferences
        if prefs:
            system += f"\n\n## User preferences\n{prefs}"

        try:
            response = self.llm.chat(
                system=system,
                user=user_message,
                history=history_msgs,
                temperature=0.7,
            )
            return {
                "ok": True,
                "reply": response.content,
                "action": "chat",
                "model": response.model,
                "provider": response.provider,
                "retrieved": bool(knowledge_ctx),
            }
        except Exception as e:
            # Graceful degradation
            return {
                "ok": True,
                "reply": (
                    f"LLM error: {e}\n\n"
                    "Falling back. You can still use notes and desktop commands."
                ),
                "action": "chat_fallback",
                "error": str(e),
            }
