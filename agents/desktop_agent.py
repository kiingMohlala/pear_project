"""
Desktop agent – tools requested from the central registry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from .base import Agent
from core.task import Task


class DesktopAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(
            name="desktop",
            description=(
                "Controls the local desktop: launch applications, open folders "
                "in the file manager, and search for files by name or pattern."
            ),
            capabilities=["desktop", "open_app", "open_folder", "search_files"],
            allowed_tools=["open_application", "open_folder", "search_files"],
            system_prompt="You are PEAR's Desktop Agent. You control local applications and files.",
            **kwargs,
        )

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        desktop_signals = [
            "open app", "open application", "launch",
            "open folder", "search files", "find file", "find files",
            "start ", "run ",
        ]
        if any(s in obj for s in desktop_signals):
            score = max(score, 0.85)
        return score

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        user_input = task.objective
        lower = user_input.lower().strip()

        m = re.search(
            r"(?:open(?:\s+app(?:lication)?)?|launch|start|run)\s+(.+)",
            lower,
            re.IGNORECASE,
        )
        if m:
            app_name = m.group(1).strip().rstrip(".")
            if not app_name.startswith("folder"):
                result = self.use_tool("open_application", app_name)
                if result.get("ok"):
                    return {
                        "ok": True,
                        "reply": result.get("message", f"Opened {app_name}"),
                        "action": "open_app",
                        "data": result,
                    }
                return {
                    "ok": False,
                    "reply": f"Couldn’t open “{app_name}”: {result.get('error')}",
                    "action": "open_app",
                    "data": result,
                }

        m = re.search(r"open\s+folder\s+(.+)", lower, re.IGNORECASE)
        if m:
            folder = m.group(1).strip().rstrip(".")
            result = self.use_tool("open_folder", folder)
            if result.get("ok"):
                return {
                    "ok": True,
                    "reply": result.get("message", f"Opened folder {folder}"),
                    "action": "open_folder",
                    "data": result,
                }
            return {
                "ok": False,
                "reply": f"Couldn’t open folder: {result.get('error')}",
                "action": "open_folder",
                "data": result,
            }

        m = re.search(
            r"(?:search|find)\s+files?\s+(.+?)(?:\s+(?:in|under|from)\s+(.+))?$",
            lower,
            re.IGNORECASE,
        )
        if m:
            pattern = m.group(1).strip()
            root = m.group(2).strip() if m.group(2) else str(Path.home())
            result = self.use_tool("search_files", root, pattern)
            if not result.get("ok"):
                return {
                    "ok": False,
                    "reply": f"Search failed: {result.get('error')}",
                    "action": "search_files",
                    "data": result,
                }
            files = result.get("results", [])
            if not files:
                return {
                    "ok": True,
                    "reply": f"No files matching “{pattern}” under {root}",
                    "action": "search_files",
                    "data": result,
                }
            preview = "\n".join(f"• {f}" for f in files[:15])
            more = f"\n… and {len(files) - 15} more" if len(files) > 15 else ""
            return {
                "ok": True,
                "reply": f"Found {len(files)} file(s):\n{preview}{more}",
                "action": "search_files",
                "data": result,
            }

        return {
            "ok": True,
            "reply": (
                "I can:\n"
                "• `open app <name>` – launch an application\n"
                "• `open folder <path>` – open a directory\n"
                "• `search files <pattern> in <path>` – find files\n"
                "What would you like me to do?"
            ),
            "action": "help",
        }
