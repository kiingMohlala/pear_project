"""
ConnectorRegistry – discovery, permissions, routing, retries.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import Connector, ConnectorResult, ConnectorStatus
from .credentials import CredentialStore

if TYPE_CHECKING:
    pass


class ConnectorRegistry:
    def __init__(self, credential_store: Optional[CredentialStore] = None):
        self._connectors: Dict[str, Connector] = {}
        self.credentials = credential_store or CredentialStore()
        self._retry_limit = 2

    def register(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector

    def get(self, name: str) -> Connector:
        if name not in self._connectors:
            raise KeyError(f"Unknown connector: {name}")
        return self._connectors[name]

    def has(self, name: str) -> bool:
        return name in self._connectors

    def list(self) -> List[Dict[str, Any]]:
        return [c.health() for c in self._connectors.values()]

    def connect(self, name: str, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        conn = self.get(name)
        creds = credentials or self.credentials.get(name)
        conn.status = ConnectorStatus.CONNECTING
        result = conn.connect(creds)
        if result.ok:
            auth = conn.authenticate(creds)
            if not auth.ok:
                conn.status = ConnectorStatus.AUTH_REQUIRED
                conn.last_error = auth.error
                return auth
            if credentials:
                self.credentials.set(name, credentials)
            conn.status = ConnectorStatus.CONNECTED
            conn.connected_at = time.time()
            return ConnectorResult(ok=True, message=f"{name} connected")
        conn.status = ConnectorStatus.ERROR
        conn.last_error = result.error
        return result

    def disconnect(self, name: str) -> ConnectorResult:
        return self.get(name).disconnect()

    def execute(
        self,
        name: str,
        action: str,
        *,
        retries: Optional[int] = None,
        **params: Any,
    ) -> ConnectorResult:
        conn = self.get(name)
        if conn.status != ConnectorStatus.CONNECTED:
            # try auto-connect with stored creds
            auto = self.connect(name)
            if not auto.ok and conn.status != ConnectorStatus.CONNECTED:
                return auto

        attempts = 0
        limit = self._retry_limit if retries is None else retries
        last: Optional[ConnectorResult] = None
        while attempts <= limit:
            try:
                from ..tracing import get_tracer
                with get_tracer().span(
                    f"connector.{name}.{action}",
                    kind="tool",
                    connector=name,
                    action=action,
                ):
                    last = conn.execute(action, **params)
            except Exception as e:
                last = ConnectorResult(ok=False, error=str(e))
            if last and last.ok:
                return last
            attempts += 1
            if attempts <= limit:
                time.sleep(min(0.5 * attempts, 2.0))
        return last or ConnectorResult(ok=False, error="execute failed")

    def auth_status(self) -> Dict[str, Any]:
        return {
            "connectors": self.list(),
            "credentials": self.credentials.status(),
        }
