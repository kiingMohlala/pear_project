"""
Desktop Agent (v0.70) – secure local automation.

Workspace sandbox, permission groups, approval for destructive ops,
tracing on every action.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Agent
from core.task import Task
from core.desktop import (
    Workspace,
    PERM_GROUPS,
    list_directory,
    copy_file,
    move_file,
    rename_file,
    delete_file,
    create_folder,
    get_system_info,
    take_screenshot,
)


class DesktopAgent(Agent):
    def __init__(self, workspace: Optional[Workspace] = None, **kwargs):
        super().__init__(
            name="desktop",
            description=(
                "Controls the local desktop within a sandbox workspace: list/copy/move/"
                "rename/delete (trash) files, create folders, launch apps, search files, "
                "system info, and screenshots. Destructive actions require approval."
            ),
            capabilities=[
                "desktop",
                "files",
                "applications",
                "open_app",
                "open_folder",
                "search_files",
            ],
            allowed_tools=[
                "open_application",
                "open_folder",
                "search_files",
                "list_directory",
                "copy_file",
                "move_file",
                "rename_file",
                "delete_file",
                "create_folder",
                "get_system_info",
                "take_screenshot",
            ],
            system_prompt="You are PEAR's Desktop Agent. Prefer workspace paths; never permanent-delete.",
            **kwargs,
        )
        self.workspace = workspace or Workspace()
        self.permissions.grant("chat")
        # Grant read + write by default; delete/launch/capture need explicit or confirm
        self.permissions.grant_group("desktop_read")
        self.permissions.grant_group("desktop_write")
        self.permissions.grant_group("desktop_launch")
        # delete & capture: grant but policy=confirm
        self.permissions.grant_group("desktop_delete")
        self.permissions.grant_group("desktop_capture")
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "open app", "open application", "launch", "open folder",
            "search files", "find file", "list dir", "list folder",
            "copy file", "move file", "rename", "delete file", "trash",
            "create folder", "mkdir", "screenshot", "system info",
            "workspace", "desktop",
        ]
        if any(s in obj for s in signals):
            score = max(score, 0.85)
        return score

    def _span(self, name: str, **attrs):
        try:
            from core.tracing import get_tracer
            return get_tracer().span(name, kind="tool", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        text = task.objective
        lower = text.lower().strip()

        # approvals: "approve desktop <id>"
        m = re.match(r"approve\s+desktop\s+(\w+)", lower)
        if m:
            return self._approve(m.group(1))

        if lower in ("desktop help", "desktop", "/desktop"):
            return self._help()

        if "system info" in lower or "system information" in lower:
            return self._sysinfo()

        if "screenshot" in lower:
            return self._screenshot(text)

        if lower.startswith("workspace") or "show workspace" in lower:
            return self._workspace_info(text)

        # list
        m = re.search(r"(?:list\s+(?:dir|directory|folder)|ls)\s+(.+)", lower)
        if m:
            return self._list(m.group(1).strip().rstrip("."))

        # create folder
        m = re.search(r"(?:create\s+folder|mkdir)\s+(.+)", lower)
        if m:
            return self._mkdir(m.group(1).strip().rstrip("."))

        # copy
        m = re.search(r"copy\s+(?:file\s+)?(.+?)\s+to\s+(.+)", lower)
        if m:
            return self._copy(m.group(1).strip(), m.group(2).strip().rstrip("."))

        # move
        m = re.search(r"move\s+(?:file\s+)?(.+?)\s+to\s+(.+)", lower)
        if m:
            return self._move(m.group(1).strip(), m.group(2).strip().rstrip("."))

        # rename
        m = re.search(r"rename\s+(.+?)\s+to\s+(.+)", lower)
        if m:
            return self._rename(m.group(1).strip(), m.group(2).strip().rstrip("."))

        # delete
        m = re.search(r"(?:delete|trash|remove)\s+(?:file\s+)?(.+)", lower)
        if m:
            return self._delete(m.group(1).strip().rstrip("."))

        # open app
        m = re.search(
            r"(?:open(?:\s+app(?:lication)?)?|launch|start|run)\s+(.+)",
            lower,
            re.I,
        )
        if m:
            app_name = m.group(1).strip().rstrip(".")
            if not app_name.startswith("folder"):
                return self._open_app(app_name)

        # open folder
        m = re.search(r"open\s+folder\s+(.+)", lower, re.I)
        if m:
            return self._open_folder(m.group(1).strip().rstrip("."))

        # search
        m = re.search(
            r"(?:search|find)\s+files?\s+(.+?)(?:\s+(?:in|under|from)\s+(.+))?$",
            lower,
            re.I,
        )
        if m:
            pattern = m.group(1).strip()
            root = m.group(2).strip() if m.group(2) else self.workspace.list_roots()[0]
            return self._search(pattern, root)

        return self._help()

    def _help(self) -> Dict[str, Any]:
        roots = ", ".join(self.workspace.list_roots())
        return {
            "ok": True,
            "reply": (
                "Desktop commands (sandboxed):\n"
                "• list dir <path>\n"
                "• create folder <path>\n"
                "• copy <src> to <dest> · move · rename · delete/trash\n"
                "• open app <name> · open folder <path>\n"
                "• search files <pattern> in <path>\n"
                "• system info · screenshot\n"
                "• workspace · workspace add <path>\n"
                f"Workspace roots: {roots}\n"
                "Destructive ops may require: approve desktop <id>"
            ),
            "action": "help",
        }

    def _workspace_info(self, text: str) -> Dict[str, Any]:
        lower = text.lower()
        m = re.search(r"workspace\s+add\s+(.+)", lower)
        if m:
            p = self.workspace.add_root(m.group(1).strip())
            return {"ok": True, "reply": f"Added workspace root: {p}", "action": "workspace_add"}
        return {
            "ok": True,
            "reply": "Workspace roots:\n" + "\n".join(f"• {r}" for r in self.workspace.list_roots()),
            "action": "workspace",
            "roots": self.workspace.list_roots(),
        }

    def _sysinfo(self) -> Dict[str, Any]:
        with self._span("desktop.system_info"):
            self.permissions.require("get_system_info")
            info = get_system_info()
        lines = [f"- {k}: {v}" for k, v in info.items() if k != "ok"]
        return {"ok": True, "reply": "## System\n" + "\n".join(lines), "action": "system_info", "data": info}

    def _screenshot(self, text: str) -> Dict[str, Any]:
        with self._span("desktop.screenshot"):
            self.permissions.require("take_screenshot")
            if self.permissions.policy("take_screenshot") == "never":
                return {"ok": False, "reply": "Screenshot policy is never.", "action": "denied"}
            result = take_screenshot(workspace=self.workspace)
        if result.get("ok"):
            return {"ok": True, "reply": result.get("message", "Screenshot taken"), "action": "screenshot", "data": result}
        return {"ok": False, "reply": result.get("error", "Screenshot failed"), "action": "screenshot", "data": result}

    def _list(self, path: str) -> Dict[str, Any]:
        with self._span("desktop.list_directory", path=path):
            self.permissions.require("list_directory")
            # default relative paths into workspace
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = self.workspace.roots[0] / p
            result = list_directory(p, workspace=self.workspace)
        if not result.get("ok"):
            return {"ok": False, "reply": result.get("error"), "action": "list_directory"}
        lines = []
        for e in result.get("entries", [])[:40]:
            mark = "📁" if e["type"] == "dir" else "📄"
            lines.append(f"{mark} {e['name']}")
        more = f"\n… {result['count'] - 40} more" if result.get("count", 0) > 40 else ""
        return {
            "ok": True,
            "reply": f"**{result['path']}** ({result['count']} entries)\n" + "\n".join(lines) + more,
            "action": "list_directory",
            "data": result,
        }

    def _mkdir(self, path: str) -> Dict[str, Any]:
        with self._span("desktop.create_folder", path=path):
            self.permissions.require("create_folder")
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = self.workspace.roots[0] / p
            result = create_folder(p, workspace=self.workspace)
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error"),
            "action": "create_folder",
            "data": result,
        }

    def _copy(self, src: str, dest: str) -> Dict[str, Any]:
        with self._span("desktop.copy_file", src=src, dest=dest):
            self.permissions.require("copy_file")
            s, d = self._resolve_pair(src, dest)
            result = copy_file(s, d, workspace=self.workspace, allow_outside=False)
        return self._maybe_approval("copy_file", result)

    def _move(self, src: str, dest: str) -> Dict[str, Any]:
        with self._span("desktop.move_file", src=src, dest=dest):
            self.permissions.require("move_file")
            s, d = self._resolve_pair(src, dest)
            result = move_file(s, d, workspace=self.workspace, allow_outside=False)
        return self._maybe_approval("move_file", result)

    def _rename(self, path: str, new_name: str) -> Dict[str, Any]:
        with self._span("desktop.rename_file", path=path):
            self.permissions.require("rename_file")
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = self.workspace.roots[0] / p
            result = rename_file(p, new_name, workspace=self.workspace)
        return self._maybe_approval("rename_file", result)

    def _delete(self, path: str) -> Dict[str, Any]:
        with self._span("desktop.delete_file", path=path):
            self.permissions.require("delete_file")
            if self.permissions.policy("delete_file") == "never":
                return {"ok": False, "reply": "Delete policy is never.", "action": "denied"}
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = self.workspace.roots[0] / p
            # Always require approval token for delete
            approval_id = f"del_{abs(hash(str(p))) % 10_000_000}"
            self.pending_approvals[approval_id] = {
                "action": "delete_file",
                "path": str(p),
            }
            return {
                "ok": True,
                "reply": (
                    f"Delete requires approval.\n"
                    f"Path: {p}\n"
                    f"Say: approve desktop {approval_id}"
                ),
                "action": "needs_approval",
                "approval_id": approval_id,
            }

    def _approve(self, approval_id: str) -> Dict[str, Any]:
        pending = self.pending_approvals.pop(approval_id, None)
        if not pending:
            return {"ok": False, "reply": f"Unknown approval id: {approval_id}", "action": "approve"}
        if pending["action"] == "delete_file":
            with self._span("desktop.delete_file.approved", path=pending["path"]):
                result = delete_file(pending["path"], workspace=self.workspace, use_trash=True)
            return {
                "ok": result.get("ok", False),
                "reply": result.get("message") or result.get("error"),
                "action": "delete_file",
                "data": result,
            }
        if pending["action"] in ("copy_file", "move_file"):
            fn = copy_file if pending["action"] == "copy_file" else move_file
            with self._span(f"desktop.{pending['action']}.approved"):
                result = fn(
                    pending["src"],
                    pending["dest"],
                    workspace=self.workspace,
                    allow_outside=True,
                )
            return {
                "ok": result.get("ok", False),
                "reply": result.get("message") or result.get("error"),
                "action": pending["action"],
                "data": result,
            }
        return {"ok": False, "reply": "Unsupported approval action", "action": "approve"}

    def _maybe_approval(self, action: str, result: Dict[str, Any]) -> Dict[str, Any]:
        if result.get("needs_approval"):
            approval_id = f"{action[:3]}_{abs(hash(result.get('message', ''))) % 10_000_000}"
            self.pending_approvals[approval_id] = {
                "action": action,
                "src": result.get("src") or result.get("path"),
                "dest": result.get("dest"),
                "message": result.get("message"),
            }
            return {
                "ok": True,
                "reply": (
                    f"{result.get('message')}\n"
                    f"Say: approve desktop {approval_id}"
                ),
                "action": "needs_approval",
                "approval_id": approval_id,
            }
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error"),
            "action": action,
            "data": result,
        }

    def _resolve_pair(self, src: str, dest: str):
        s = Path(src).expanduser()
        d = Path(dest).expanduser()
        if not s.is_absolute():
            s = self.workspace.roots[0] / s
        if not d.is_absolute():
            d = self.workspace.roots[0] / d
        return s, d

    def _open_app(self, app_name: str) -> Dict[str, Any]:
        with self._span("desktop.open_application", app=app_name):
            self.permissions.require("open_application")
            result = self.use_tool("open_application", app_name)
        if result.get("ok"):
            return {"ok": True, "reply": result.get("message", f"Opened {app_name}"), "action": "open_app", "data": result}
        return {"ok": False, "reply": f"Couldn’t open “{app_name}”: {result.get('error')}", "action": "open_app", "data": result}

    def _open_folder(self, folder: str) -> Dict[str, Any]:
        with self._span("desktop.open_folder", path=folder):
            self.permissions.require("open_folder")
            p = Path(folder).expanduser()
            if not p.is_absolute():
                p = self.workspace.roots[0] / p
            result = self.use_tool("open_folder", str(p))
        if result.get("ok"):
            return {"ok": True, "reply": result.get("message", f"Opened {p}"), "action": "open_folder", "data": result}
        return {"ok": False, "reply": result.get("error"), "action": "open_folder", "data": result}

    def _search(self, pattern: str, root: str) -> Dict[str, Any]:
        with self._span("desktop.search_files", pattern=pattern, root=root):
            self.permissions.require("search_files")
            p = Path(root).expanduser()
            if not p.is_absolute():
                p = self.workspace.roots[0] / p
            # Prefer sandbox
            if not self.workspace.is_inside(p):
                return {
                    "ok": False,
                    "reply": f"Search root outside workspace: {p}. Add it with: workspace add {p}",
                    "action": "search_files",
                }
            result = self.use_tool("search_files", str(p), pattern)
        if not result.get("ok"):
            return {"ok": False, "reply": result.get("error"), "action": "search_files", "data": result}
        files = result.get("results", [])
        if not files:
            return {"ok": True, "reply": f"No files matching “{pattern}” under {p}", "action": "search_files"}
        preview = "\n".join(f"• {f}" for f in files[:15])
        more = f"\n… and {len(files) - 15} more" if len(files) > 15 else ""
        return {
            "ok": True,
            "reply": f"Found {len(files)} file(s):\n{preview}{more}",
            "action": "search_files",
            "data": result,
        }
