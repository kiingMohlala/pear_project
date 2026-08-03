"""Optional Google Drive connector scaffold (v3.1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class GoogleDriveConnector(Connector):
    name = "gdrive"
    description = "List and fetch Google Drive files (optional)"
    provider = "google"
    capabilities = [
        ConnectorCapability("list_files", "List files", "gdrive_read"),
        ConnectorCapability("get_file", "Get file metadata/text", "gdrive_read"),
        ConnectorCapability("status", "Status", "gdrive_read"),
    ]

    def __init__(self, token: str = ""):
        super().__init__()
        self.token = token

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or {}
        self.token = str(creds.get("token") or creds.get("access_token") or self.token)
        if not self.token:
            self.status = ConnectorStatus.DISCONNECTED
            return ConnectorResult(ok=False, error="Google Drive token required (disabled)")
        self.status = ConnectorStatus.CONNECTED
        return ConnectorResult(ok=True, message="Google Drive ready")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return self.connect(credentials)

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "status":
            return ConnectorResult(ok=True, data={"enabled": bool(self.token)})
        if not self.token:
            return ConnectorResult(ok=False, error="gdrive disabled")
        if action == "list_files":
            return ConnectorResult(ok=True, data={"files": [{"id": "file_demo", "name": "Sample.pdf"}]})
        if action == "get_file":
            return ConnectorResult(ok=True, data={"id": params.get("file_id"), "name": "Sample.pdf", "text": ""})
        return ConnectorResult(ok=False, error=f"unknown action: {action}")
