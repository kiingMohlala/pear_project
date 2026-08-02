"""
Central Tool Registry.

Tools live here, not inside individual agents.
Agents declare which tool *names* they are allowed to use;
the registry holds the actual callables and metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    tags: List[str] = field(default_factory=list)
    # Optional: which permission key is required to run this tool
    requires_permission: Optional[str] = None


class ToolRegistry:
    """
    Singleton-style registry (one instance per PEAR process is typical).
    """

    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str = "",
        tags: Optional[List[str]] = None,
        requires_permission: Optional[str] = None,
    ) -> None:
        self._tools[name] = ToolSpec(
            name=name,
            description=description or name,
            fn=fn,
            tags=tags or [],
            requires_permission=requires_permission,
        )

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name]

    def call(self, name: str, *args, **kwargs) -> Any:
        try:
            from .tracing import get_tracer
            tracer = get_tracer()
        except Exception:
            return self.get(name).fn(*args, **kwargs)
        with tracer.span(f"tool.{name}", kind="tool", tool=name):
            return self.get(name).fn(*args, **kwargs)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        result = []
        for spec in self._tools.values():
            if tag and tag not in spec.tags:
                continue
            result.append({
                "name": spec.name,
                "description": spec.description,
                "tags": list(spec.tags),
                "requires_permission": spec.requires_permission,
            })
        return result

    def names(self) -> Set[str]:
        return set(self._tools.keys())


def build_default_registry() -> ToolRegistry:
    """Register the built-in tools shipped with PEAR."""
    from core import tools as t

    reg = ToolRegistry()

    reg.register(
        "read_document",
        t.read_document,
        description="Extract text from a PDF or DOCX file",
        tags=["files", "documents"],
        requires_permission="read_file",
    )
    reg.register(
        "summarize_text",
        t.summarize_text,
        description="Produce a short extractive summary of text",
        tags=["files", "nlp"],
        requires_permission="summarize",
    )
    reg.register(
        "open_application",
        t.open_application,
        description="Launch a desktop application by name",
        tags=["desktop"],
        requires_permission="open_app",
    )
    reg.register(
        "open_folder",
        t.open_folder,
        description="Open a folder in the system file manager",
        tags=["desktop"],
        requires_permission="open_folder",
    )
    reg.register(
        "search_files",
        t.search_files,
        description="Search for files under a root path with a glob pattern",
        tags=["desktop", "files"],
        requires_permission="search_files",
    )


    reg.register(
        "list_directory",
        t.list_directory,
        description="List files and folders in a directory",
        tags=["desktop", "files", "read"],
        requires_permission="list_directory",
    )
    reg.register(
        "copy_file",
        t.copy_file,
        description="Copy a file or folder",
        tags=["desktop", "files", "write"],
        requires_permission="copy_file",
    )
    reg.register(
        "move_file",
        t.move_file,
        description="Move a file or folder",
        tags=["desktop", "files", "write"],
        requires_permission="move_file",
    )
    reg.register(
        "rename_file",
        t.rename_file,
        description="Rename a file or folder",
        tags=["desktop", "files", "write"],
        requires_permission="rename_file",
    )
    reg.register(
        "delete_file",
        t.delete_file,
        description="Move a file to trash (safe delete)",
        tags=["desktop", "files", "delete"],
        requires_permission="delete_file",
    )
    reg.register(
        "create_folder",
        t.create_folder,
        description="Create a folder",
        tags=["desktop", "files", "write"],
        requires_permission="create_folder",
    )
    reg.register(
        "get_system_info",
        t.get_system_info,
        description="Basic system information",
        tags=["desktop", "read"],
        requires_permission="get_system_info",
    )
    reg.register(
        "take_screenshot",
        t.take_screenshot,
        description="Capture screenshot to workspace",
        tags=["desktop", "capture"],
        requires_permission="take_screenshot",
    )
    return reg
