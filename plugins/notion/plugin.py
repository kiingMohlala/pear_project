from __future__ import annotations

from typing import Any, Dict, Optional

from core.plugins.base import Plugin
from core.connectors.base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class NotionConnector(Connector):
    name = "notion"
    description = "Notion API stub"
    provider = "notion"
    capabilities = [
        ConnectorCapability("list_pages", "List pages", "notion_read"),
        ConnectorCapability("create_page", "Create page", "notion_write", sensitive=True),
    ]

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        self._token = (credentials or {}).get("token", "")
        if not self._token:
            return ConnectorResult(ok=False, error="Notion token required")
        self.status = ConnectorStatus.CONNECTED
        return ConnectorResult(ok=True, message="Notion connected (stub)")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return ConnectorResult(ok=True, message="ok")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "list_pages":
            return ConnectorResult(ok=True, data={"pages": []}, message="No pages (stub)")
        if action == "create_page":
            return ConnectorResult(ok=True, message=f"[stub] Created page {params.get('title')}")
        return ConnectorResult(ok=False, error=f"Unknown action {action}")


class PluginImpl(Plugin):
    def load(self, api):
        api.register_connector(NotionConnector())
        api.register_command("notion", lambda arg: "Notion plugin: use /connect notion token=...")
