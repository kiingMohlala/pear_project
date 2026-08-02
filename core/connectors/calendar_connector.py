"""Calendar connector – local JSON calendar store (provider-ready)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class CalendarConnector(Connector):
    name = "calendar"
    description = "Local calendar events (ICS/API providers can plug in later)"
    provider = "local_json"
    capabilities = [
        ConnectorCapability("list_events", "List events", "calendar_read"),
        ConnectorCapability("create_event", "Create event", "calendar_write", sensitive=True),
        ConnectorCapability("delete_event", "Delete event", "calendar_write", sensitive=True),
    ]

    def __init__(self, store_path: Optional[Path] = None):
        super().__init__()
        self.store_path = Path(store_path) if store_path else Path.home() / ".pear" / "calendar.json"
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._events: List[Dict[str, Any]] = []

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        self._load()
        self.status = ConnectorStatus.CONNECTED
        self.connected_at = time.time()
        return ConnectorResult(ok=True, message=f"Calendar loaded ({len(self._events)} events)")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        return ConnectorResult(ok=True, message="Local calendar — no auth")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "list_events":
            return ConnectorResult(ok=True, data={"events": list(self._events)})
        if action == "create_event":
            title = params.get("title") or params.get("summary") or "Event"
            event = {
                "id": f"evt_{uuid.uuid4().hex[:8]}",
                "title": title,
                "start": params.get("start"),
                "end": params.get("end"),
                "description": params.get("description") or "",
                "created_at": time.time(),
            }
            self._events.append(event)
            self._save()
            return ConnectorResult(ok=True, data=event, message=f"Created {title}")
        if action == "delete_event":
            eid = params.get("id")
            before = len(self._events)
            self._events = [e for e in self._events if e.get("id") != eid]
            self._save()
            return ConnectorResult(ok=True, message=f"Deleted {before - len(self._events)} event(s)")
        return ConnectorResult(ok=False, error=f"Unknown action: {action}")

    def _load(self) -> None:
        if self.store_path.exists():
            try:
                self._events = json.loads(self.store_path.read_text(encoding="utf-8"))
            except Exception:
                self._events = []
        else:
            self._events = []

    def _save(self) -> None:
        self.store_path.write_text(json.dumps(self._events, indent=2), encoding="utf-8")
