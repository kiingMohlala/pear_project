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
            self._append_and_save(event)
            return ConnectorResult(ok=True, data=event, message=f"Created {title}")
        if action == "delete_event":
            eid = params.get("id")
            before = self._remove_and_save(eid)
            return ConnectorResult(ok=True, message=f"Deleted {before} event(s)")
        return ConnectorResult(ok=False, error=f"Unknown action: {action}")

    def _load(self) -> None:
        if self.store_path.exists():
            try:
                self._events = json.loads(self.store_path.read_text(encoding="utf-8"))
            except Exception:
                self._events = []
        else:
            self._events = []

    # PEAR 3.2 Task 015: CalendarConnector shares Memory/Learning/
    # SelfImprovement's exact ownership shape -- one CalendarConnector
    # instance is built once per Orchestrator (build_default_connectors()
    # runs once in Orchestrator.__init__, not per request or per
    # connector call) and reused across that same user's later requests.
    # There's a single in-memory `self._events` list, never reconstructed
    # mid-session, so -- like Memory -- there's no stale-snapshot problem
    # a reload-before-write could fix; the actual defect (confirmed by
    # reproduction: corrupted/torn JSON under concurrent create_event
    # calls, not silently-lost-but-valid data) is two threads' bare
    # write_text() calls interleaving on the same path. Same fix shape
    # as Memory/Learning/SelfImprove for the same reason: lock the
    # mutate-then-save cycle, build the snapshot to serialize *inside*
    # the lock (not before it) so the last writer is always the most
    # complete one, and write atomically so a write that does lose the
    # lock race still leaves a valid, complete prior file rather than a
    # torn one.
    def _append_and_save(self, event: Dict[str, Any]) -> None:
        from core.security import locked_json_store, atomic_write_text
        with locked_json_store(self.store_path):
            self._load()
            self._events.append(event)
            atomic_write_text(self.store_path, json.dumps(self._events, indent=2))

    def _remove_and_save(self, eid: Optional[str]) -> int:
        from core.security import locked_json_store, atomic_write_text
        with locked_json_store(self.store_path):
            self._load()
            before = len(self._events)
            self._events = [e for e in self._events if e.get("id") != eid]
            atomic_write_text(self.store_path, json.dumps(self._events, indent=2))
            return before - len(self._events)

    def _save(self) -> None:
        # Kept for any external caller/test that still expects a bare
        # _save() to persist the current in-memory _events as-is (e.g.
        # after directly mutating self._events, outside create/delete).
        # Locked and atomic for the same reason as the two methods above.
        from core.security import locked_json_store, atomic_write_text
        with locked_json_store(self.store_path):
            atomic_write_text(self.store_path, json.dumps(self._events, indent=2))
