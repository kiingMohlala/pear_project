"""Optional Notion connector (v3.1 scaffold)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class NotionConnector(Connector):
    name = "notion"
    description = "Read/write Notion pages (optional)"
    provider = "notion"
    capabilities = [
        ConnectorCapability("search", "Search pages", "notion_read"),
        ConnectorCapability("get_page", "Fetch page content", "notion_read"),
        ConnectorCapability("status", "Status", "notion_read"),
    ]

    def __init__(self, token: str = ""):
        super().__init__()
        self.token = token

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or {}
        self.token = str(creds.get("token") or creds.get("api_key") or self.token)
        if not self.token:
            self.status = ConnectorStatus.DISCONNECTED
            return ConnectorResult(ok=False, error="Notion token required (disabled)")
        self.status = ConnectorStatus.CONNECTED
        return ConnectorResult(ok=True, message="Notion ready")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return self.connect(credentials)

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "status":
            return ConnectorResult(ok=True, data={"enabled": bool(self.token)})
        if not self.token:
            return ConnectorResult(ok=False, error="Notion disabled")
        if action == "search":
            q = params.get("query") or ""
            return ConnectorResult(ok=True, data={"results": [{"title": f"Demo page for {q}", "id": "page_demo"}]})
        if action == "get_page":
            return ConnectorResult(ok=True, data={"id": params.get("page_id"), "text": "Demo Notion content"})
        return ConnectorResult(ok=False, error=f"unknown action: {action}")
