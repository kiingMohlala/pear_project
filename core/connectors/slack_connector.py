"""Optional Slack connector (v3.1 scaffold) — disabled until token + base configured."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class SlackConnector(Connector):
    name = "slack"
    description = "Post messages and list channels (optional)"
    provider = "slack"
    capabilities = [
        ConnectorCapability("post_message", "Post a channel message", "slack_write", sensitive=True),
        ConnectorCapability("list_channels", "List channels", "slack_read"),
        ConnectorCapability("status", "Status", "slack_read"),
    ]

    def __init__(self, token: str = "", base_url: str = "https://slack.com/api"):
        super().__init__()
        self.token = token
        self.base_url = base_url.rstrip("/")

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or {}
        self.token = str(creds.get("token") or creds.get("bot_token") or self.token)
        if not self.token:
            self.status = ConnectorStatus.DISCONNECTED
            return ConnectorResult(ok=False, error="Slack token required (connector disabled)")
        self.status = ConnectorStatus.CONNECTED
        self.connected_at = __import__("time").time()
        return ConnectorResult(ok=True, message="Slack ready")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return self.connect(credentials)

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "status":
            return ConnectorResult(ok=True, data={"enabled": bool(self.token), "status": self.status.value})
        if not self.token:
            return ConnectorResult(ok=False, error="Slack disabled")
        if action == "list_channels":
            # dry-run offline
            return ConnectorResult(ok=True, data={"channels": [{"id": "C_DEMO", "name": "general"}]})
        if action == "post_message":
            if not params.get("approved"):
                return ConnectorResult(
                    ok=False,
                    needs_approval=True,
                    message="Slack post requires approval",
                    approval_payload={"action": "post_message", **params},
                )
            return ConnectorResult(ok=True, data={"ok": True, "channel": params.get("channel"), "dry_run": True})
        return ConnectorResult(ok=False, error=f"unknown action: {action}")
