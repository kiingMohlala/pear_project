"""Local Files connector – bridges to workspace/desktop file ops."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus
from ..desktop import Workspace, list_directory, copy_file, create_folder


class LocalFilesConnector(Connector):
    name = "local_files"
    description = "Read/write files inside the PEAR workspace sandbox"
    provider = "local"
    capabilities = [
        ConnectorCapability("list", "List directory", "files_read"),
        ConnectorCapability("read", "Read file text", "files_read"),
        ConnectorCapability("write", "Write file text", "files_write", sensitive=True),
        ConnectorCapability("mkdir", "Create folder", "files_write"),
    ]

    def __init__(self, workspace: Optional[Workspace] = None):
        super().__init__()
        self.workspace = workspace or Workspace()

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        roots = self.workspace.list_roots()
        self.metadata["roots"] = roots
        self.status = ConnectorStatus.CONNECTED
        self.connected_at = __import__("time").time()
        return ConnectorResult(ok=True, message=f"Workspace ready: {roots}")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return ConnectorResult(ok=True, message="No auth required for local files")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "list":
            path = params.get("path") or self.workspace.list_roots()[0]
            r = list_directory(path, workspace=self.workspace)
            return ConnectorResult(ok=r.get("ok", False), data=r, error=r.get("error"), message=r.get("message", ""))
        if action == "read":
            path = Path(params["path"]).expanduser()
            self.workspace.require_inside(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            return ConnectorResult(ok=True, data={"path": str(path), "text": text[:50000]})
        if action == "write":
            path = Path(params["path"]).expanduser()
            if not self.workspace.is_inside(path):
                return ConnectorResult(
                    ok=False,
                    needs_approval=True,
                    error="outside_workspace",
                    message=f"Write outside sandbox requires approval: {path}",
                    approval_payload={"action": "write", "path": str(path)},
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.get("text", ""), encoding="utf-8")
            return ConnectorResult(ok=True, message=f"Wrote {path}")
        if action == "mkdir":
            path = params.get("path")
            r = create_folder(path, workspace=self.workspace)
            return ConnectorResult(ok=r.get("ok", False), data=r, message=r.get("message", ""), error=r.get("error"))
        return ConnectorResult(ok=False, error=f"Unknown action: {action}")
