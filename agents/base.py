"""
Generic Agent base class – every specialized agent inherits from this.

Agents do NOT own tool implementations.
They declare allowed tool names and call them through the ToolRegistry.
Agents never call other agents directly – they request subtasks via the planner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.memory import Memory
from core.permissions import Permissions
from core.task import Task, TaskStatus
from core.events import EventBus, EventType

if TYPE_CHECKING:
    from core.tool_registry import ToolRegistry
    from core.orchestrator import Orchestrator


class Agent(ABC):
    def __init__(
        self,
        name: str,
        description: str = "",
        capabilities: Optional[List[str]] = None,
        allowed_tools: Optional[List[str]] = None,
        memory: Optional[Memory] = None,
        permissions: Optional[Permissions] = None,
        system_prompt: str = "",
    ):
        self.name = name
        self.description = description or f"{name} agent"
        self.capabilities: List[str] = capabilities or []
        self.allowed_tools: List[str] = allowed_tools or []
        self.memory = memory or Memory()
        self.permissions = permissions or Permissions()
        self.system_prompt = system_prompt or f"You are {name}, a helpful PEAR agent."

        # Injected by Orchestrator on register()
        self.registry: Optional["ToolRegistry"] = None
        self.events: Optional[EventBus] = None
        self.planner: Optional["Orchestrator"] = None

    def think(self, user_input: str, task: Optional[Task] = None, **kwargs) -> Dict[str, Any]:
        if task is None:
            task = Task(objective=user_input)

        self.memory.working.add("user", user_input)

        try:
            self.permissions.require("chat")
        except PermissionError as e:
            task.fail(str(e))
            self._emit(EventType.TASK_FAILED, {"task_id": task.id, "error": str(e)})
            result = {"ok": False, "agent": self.name, "error": str(e), "task_id": task.id}
            self.memory.working.add("assistant", str(e))
            return result

        if not task.assigned_agent:
            task.assign(self.name)
        task.start()
        self._emit(EventType.TASK_STARTED, {
            "task_id": task.id,
            "agent": self.name,
            "objective": task.objective,
        })

        try:
            response = self._process(task, **kwargs)
        except Exception as e:
            task.fail(str(e))
            self._emit(EventType.TASK_FAILED, {"task_id": task.id, "error": str(e)})
            result = {"ok": False, "agent": self.name, "error": str(e), "task_id": task.id}
            self.memory.working.add("assistant", f"Error: {e}")
            return result

        if isinstance(response, str):
            response = {"ok": True, "agent": self.name, "reply": response}
        else:
            response.setdefault("ok", True)
            response.setdefault("agent", self.name)

        response["task_id"] = task.id
        reply_text = response.get("reply") or response.get("message") or str(response)

        if response.get("ok"):
            task.complete(response)
            self._emit(EventType.TASK_COMPLETED, {
                "task_id": task.id,
                "agent": self.name,
                "reply_preview": reply_text[:120],
            })
        else:
            task.fail(response.get("error") or reply_text)
            self._emit(EventType.TASK_FAILED, {
                "task_id": task.id,
                "error": response.get("error") or reply_text,
            })

        self.memory.working.add("assistant", reply_text, agent=self.name, task_id=task.id)
        return response

    @abstractmethod
    def _process(self, task: Task, **kwargs) -> Dict[str, Any] | str:
        ...

    def use_tool(self, tool_name: str, *args, **kwargs) -> Any:
        if tool_name not in self.allowed_tools:
            raise PermissionError(
                f"Agent '{self.name}' is not allowed to use tool '{tool_name}'"
            )
        if self.registry is None:
            raise RuntimeError("Tool registry not injected – agent not registered with Orchestrator")

        spec = self.registry.get(tool_name)
        if spec.requires_permission:
            self.permissions.require(spec.requires_permission)

        self._emit(EventType.TOOL_CALLED, {
            "tool": tool_name,
            "agent": self.name,
            "args_preview": str(args)[:80],
        })

        try:
            result = self.registry.call(tool_name, *args, **kwargs)
            self._emit(EventType.TOOL_FINISHED, {
                "tool": tool_name,
                "agent": self.name,
                "ok": True,
            })
            return result
        except Exception as e:
            self._emit(EventType.TOOL_FINISHED, {
                "tool": tool_name,
                "agent": self.name,
                "ok": False,
                "error": str(e),
            })
            raise

    def request_subtask(
        self,
        objective: str,
        *,
        required_capabilities: Optional[List[str]] = None,
        parent: Optional[Task] = None,
    ) -> Dict[str, Any]:
        """
        Ask the planner to create and run a child task.
        Agents must never import or call other agents themselves.
        """
        if self.planner is None:
            raise RuntimeError("Planner not injected – cannot request subtasks")

        self._emit(EventType.SUBTASK_REQUESTED, {
            "from_agent": self.name,
            "objective": objective,
            "parent_id": parent.id if parent else None,
        })

        return self.planner.route(
            objective,
            required_capabilities=required_capabilities,
            parent_task=parent,
        )

    def can_handle(self, task: Task) -> float:
        if task.required_capabilities:
            overlap = set(task.required_capabilities) & set(self.capabilities)
            if not overlap:
                return 0.0
            return len(overlap) / len(task.required_capabilities)

        obj = task.objective.lower()
        score = 0.0
        for cap in self.capabilities:
            if cap.replace("_", " ") in obj or cap in obj:
                score += 0.4
        for token in self.description.lower().split():
            if len(token) > 3 and token in obj:
                score += 0.15
        return min(score, 1.0)

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities

    def _emit(self, event_type: EventType, payload: Dict[str, Any]) -> None:
        if self.events:
            self.events.emit(event_type, payload, source=self.name)

    def __repr__(self) -> str:
        return f"<Agent name={self.name!r} caps={self.capabilities}>"
