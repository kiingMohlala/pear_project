"""
Personal / general-purpose agent – chat, notes, file reading.
Uses the shared LLM abstraction + knowledge retrieval.
"""

from __future__ import annotations

from pathlib import Path
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

    def handle_file_upload(self, path: str) -> Dict[str, Any]:
        """
        Read + summarize an uploaded document, then store it in the
        knowledge store. ui/app.py used to call core.tools functions
        directly for this, bypassing the agent, the registry's permission
        checks, and event logging entirely. Routing it through use_tool()
        closes that gap.
        """
        from core.events import EventType

        task = Task(objective=f"Upload and summarize file: {path}")
        task.assign(self.name)
        if self.planner is not None:
            self.planner.task_log.append(task)
        self._emit(EventType.TASK_CREATED, {"task_id": task.id, "objective": task.objective})
        task.start()
        self._emit(EventType.TASK_STARTED, {
            "task_id": task.id, "agent": self.name, "objective": task.objective,
        })

        try:
            text = self.use_tool("read_document", path)
        except Exception as e:
            error = str(e)
            task.fail(error)
            self._emit(EventType.TASK_FAILED, {"task_id": task.id, "error": error})
            return {"ok": False, "error": error, "task_id": task.id}

        self.memory.knowledge.add_document(name=Path(path).name, text=text, source_path=str(path))
        self.memory._save()

        try:
            summary = self.use_tool("summarize_text", text)
        except Exception as e:
            summary = f"(summary unavailable: {e})"

        response = {
            "ok": True,
            "reply": summary,
            "name": Path(path).name,
            "chars": len(text),
            "task_id": task.id,
        }
        task.complete(response)
        self._emit(EventType.TASK_COMPLETED, {
            "task_id": task.id, "agent": self.name, "reply_preview": summary[:120],
        })
        return response

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
        on_token = kwargs.get("on_token")
        return self._llm_reply(user_input, on_token=on_token)

    def _llm_reply(self, user_message: str, on_token=None) -> Dict[str, Any]:
        # Retrieve relevant knowledge
        knowledge_ctx = self.memory.knowledge.build_context(user_message, max_chars=6000)

        # Working memory → chat history (exclude the just-added user turn duplicate)
        history_msgs: List[LLMMessage] = []
        for msg in self.memory.working.get_history(limit=12):
            if msg.role in ("user", "assistant"):
                history_msgs.append(LLMMessage(role=msg.role, content=msg.content))

        # Drop last user message from history – generate/stream will add current user
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

        prefs = self.memory.long_term.preferences
        if prefs:
            system += f"\n\n## User preferences\n{prefs}"

        try:
            if on_token is not None:
                response = self.llm.chat_stream(
                    system=system,
                    user=user_message,
                    history=history_msgs,
                    temperature=0.7,
                    on_token=on_token,
                )
                return {
                    "ok": True,
                    "reply": response.content,
                    "action": "chat",
                    "model": response.model,
                    "provider": response.provider,
                    "retrieved": bool(knowledge_ctx),
                    "streamed": True,
                }

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
            return {
                "ok": True,
                "reply": (
                    f"LLM error: {e}\n\n"
                    "Falling back. You can still use notes and desktop commands."
                ),
                "action": "chat_fallback",
                "error": str(e),
            }
