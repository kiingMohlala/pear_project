"""
Connector framework (v0.90) – provider-agnostic external integrations.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class ConnectorStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


@dataclass
class ConnectorCapability:
    name: str
    description: str = ""
    permission: str = ""  # e.g. email_send
    sensitive: bool = False


@dataclass
class ConnectorResult:
    ok: bool
    data: Any = None
    error: Optional[str] = None
    message: str = ""
    needs_approval: bool = False
    approval_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "message": self.message,
            "needs_approval": self.needs_approval,
            "approval_payload": self.approval_payload,
        }


class Connector(ABC):
    """
    Lifecycle: connect → authenticate → execute → disconnect
    """

    name: str = "base"
    description: str = ""
    provider: str = "generic"
    capabilities: List[ConnectorCapability] = []

    def __init__(self):
        self.status = ConnectorStatus.DISCONNECTED
        self.last_error: Optional[str] = None
        self.connected_at: Optional[float] = None
        self.metadata: Dict[str, Any] = {}

    def capability_names(self) -> Set[str]:
        return {c.name for c in self.capabilities}

    def permission_names(self) -> Set[str]:
        return {c.permission for c in self.capabilities if c.permission}

    @abstractmethod
    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        ...

    @abstractmethod
    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        ...

    @abstractmethod
    def execute(self, action: str, **params: Any) -> ConnectorResult:
        ...

    def disconnect(self) -> ConnectorResult:
        self.status = ConnectorStatus.DISCONNECTED
        self.connected_at = None
        return ConnectorResult(ok=True, message=f"{self.name} disconnected")

    def health(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "status": self.status.value,
            "capabilities": [asdict(c) for c in self.capabilities],
            "connected_at": self.connected_at,
            "last_error": self.last_error,
        }

    def require_connected(self) -> Optional[ConnectorResult]:
        if self.status != ConnectorStatus.CONNECTED:
            return ConnectorResult(
                ok=False,
                error=f"{self.name} not connected (status={self.status.value})",
            )
        return None
