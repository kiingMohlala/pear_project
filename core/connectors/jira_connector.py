"""Optional Jira connector scaffold (v3.1)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class JiraConnector(Connector):
    name = "jira"
    description = "List and create Jira issues (optional)"
    provider = "atlassian"
    capabilities = [
        ConnectorCapability("list_issues", "Search issues", "jira_read"),
        ConnectorCapability("create_issue", "Create issue", "jira_write", sensitive=True),
        ConnectorCapability("status", "Status", "jira_read"),
    ]

    def __init__(self, base_url: str = "", email: str = "", api_token: str = ""):
        super().__init__()
        self.base_url = (base_url or "").rstrip("/")
        self.email = email
        self.api_token = api_token

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or {}
        self.base_url = str(creds.get("base_url") or self.base_url).rstrip("/")
        self.email = str(creds.get("email") or self.email)
        self.api_token = str(creds.get("api_token") or creds.get("token") or self.api_token)
        if not self.base_url or not self.api_token:
            self.status = ConnectorStatus.DISCONNECTED
            return ConnectorResult(ok=False, error="Jira base_url and api_token required (disabled)")
        self.status = ConnectorStatus.CONNECTED
        return ConnectorResult(ok=True, message="Jira ready")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return self.connect(credentials)

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "status":
            return ConnectorResult(ok=True, data={"enabled": bool(self.base_url and self.api_token)})
        if not (self.base_url and self.api_token):
            return ConnectorResult(ok=False, error="jira disabled")
        if action == "list_issues":
            return ConnectorResult(ok=True, data={"issues": [{"key": "DEMO-1", "summary": "Sample issue"}]})
        if action == "create_issue":
            if not params.get("approved"):
                return ConnectorResult(
                    ok=False,
                    needs_approval=True,
                    message="Jira create_issue requires approval",
                    approval_payload={"action": "create_issue", **params},
                )
            return ConnectorResult(ok=True, data={"key": "DEMO-2", "dry_run": True})
        return ConnectorResult(ok=False, error=f"unknown action: {action}")
