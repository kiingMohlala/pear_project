"""
Permission-scoped Plugin API – the only surface plugins should use.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..orchestrator import Orchestrator
    from .manifest import PluginManifest


class PluginAPI:
    """
    Scoped facade over PEAR registries.
    Plugins cannot access arbitrary orchestrator internals.
    """

    def __init__(self, orch: "Orchestrator", manifest: "PluginManifest"):
        self._orch = orch
        self.manifest = manifest
        self._commands: Dict[str, Callable] = {}
        self._allowed = set(manifest.permissions or [])

    def _require(self, perm: str) -> None:
        if perm not in self._allowed and "*" not in self._allowed:
            raise PermissionError(
                f"Plugin '{self.manifest.name}' lacks permission '{perm}'"
            )

    # ── registration ──────────────────────────────────────────────

    def register_tool(
        self,
        name: str,
        fn: Callable,
        description: str = "",
        permission: str = "",
        tags: Optional[List[str]] = None,
    ) -> None:
        self._require("register_tool")
        self._orch.registry.register(
            name,
            fn,
            description=description or f"Plugin tool from {self.manifest.name}",
            tags=list(tags or []) + [f"plugin:{self.manifest.name}"],
            requires_permission=permission or name,
        )

    def register_agent(self, agent: Any) -> None:
        self._require("register_agent")
        self._orch.register(agent)

    def register_connector(self, connector: Any) -> None:
        self._require("register_connector")
        self._orch.connectors.register(connector)

    def register_workflow(self, workflow: Any) -> None:
        self._require("register_workflow")
        self._orch.workflows.register(workflow)

    def register_command(self, name: str, handler: Callable[[str], str]) -> None:
        """CLI command without leading slash, e.g. 'weather'."""
        self._require("register_command")
        key = name.lstrip("/").lower()
        self._commands[key] = handler
        # store on orchestrator for CLI dispatch
        cmds = getattr(self._orch, "plugin_commands", None)
        if cmds is None:
            self._orch.plugin_commands = {}
            cmds = self._orch.plugin_commands
        cmds[key] = handler

    def on_event(self, event_type: Any, listener: Callable) -> None:
        self._require("register_listener")
        self._orch.events.on(event_type, listener)

    # ── safe execution helpers ────────────────────────────────────

    def execute_tool(self, name: str, *args, **kwargs) -> Any:
        self._require("use_tools")
        return self._orch.registry.call(name, *args, **kwargs)

    def connector_execute(self, name: str, action: str, **params) -> Any:
        self._require("use_connectors")
        return self._orch.connectors.execute(name, action, **params)

    def log(self, message: str) -> None:
        try:
            from ..events import EventType
            self._orch.events.emit(
                EventType.NOTE,
                {"plugin": self.manifest.name, "message": message},
                source=f"plugin:{self.manifest.name}",
            )
        except Exception:
            pass
