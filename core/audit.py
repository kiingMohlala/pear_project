"""Audit logging for auth, permissions, admin actions (v2.40)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditLog:
    def __init__(self, path: Optional[Path] = None, enabled: bool = True):
        self.path = Path(path) if path else Path.home() / ".pear" / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._buffer: List[Dict[str, Any]] = []

    def record(
        self,
        action: str,
        *,
        actor: str = "system",
        resource: str = "",
        outcome: str = "ok",
        detail: Optional[Dict[str, Any]] = None,
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        entry = {
            "id": f"aud_{uuid.uuid4().hex[:10]}",
            "ts": time.time(),
            "action": action,
            "actor": actor,
            "resource": resource,
            "outcome": outcome,
            "detail": detail or {},
            "correlation_id": correlation_id,
        }
        if not self.enabled:
            self._buffer.append(entry)
            return entry
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            self._buffer.append(entry)
            if len(self._buffer) > 500:
                self._buffer = self._buffer[-500:]
        return entry

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self._buffer[-limit:])

    def read_file(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
