"""PEAR version and persisted-state schema checks (v3.00)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__version__ = "3.0.0"
SCHEMA_VERSION = 3  # bumped for goals/learning/workers layout

# Public API surface freeze markers (documentation / compatibility)
PUBLIC_APIS = {
    "agents": ["Agent", "think", "can_handle", "capabilities"],
    "connectors": ["Connector", "connect", "authenticate", "execute", "disconnect"],
    "plugins": ["Plugin", "PluginManager", "manifest"],
    "workflows": ["Workflow", "WorkflowStep", "WorkflowRunner"],
    "service": [
        "/health", "/ready", "/metrics", "/auth/login",
        "/v1/chat", "/v1/chat/stream", "/v1/agents", "/v1/goals",
    ],
}


def parse_version(v: str) -> Tuple[int, ...]:
    parts = []
    for p in v.replace("-rc", ".").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_compat(required: str = "3.0.0") -> bool:
    return parse_version(__version__) >= parse_version(required)


def migrate_data_dir(data_dir: Path) -> Dict[str, Any]:
    """
    Ensure persisted layout is schema-compatible.
    Non-destructive: creates marker + missing dirs; rewrites schema file.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / ".pear_schema.json"
    actions: List[str] = []
    prev = 0
    if marker.exists():
        try:
            prev = int(json.loads(marker.read_text(encoding="utf-8")).get("schema_version") or 0)
        except Exception:
            prev = 0
    for sub in ("goals", "learning", "workers", "sessions", "backups"):
        p = data_dir / sub
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            actions.append(f"mkdir {sub}")
    # v1→v2: ensure learning state file exists
    if prev < 2:
        lp = data_dir / "learning" / "learning_state.json"
        if not lp.exists():
            lp.write_text("{}", encoding="utf-8")
            actions.append("init learning_state")
    # v2→v3: ensure audit log file
    if prev < 3:
        audit = data_dir / "audit.jsonl"
        if not audit.exists():
            audit.write_text("", encoding="utf-8")
            actions.append("init audit.jsonl")
    marker.write_text(
        json.dumps({
            "schema_version": SCHEMA_VERSION,
            "pear_version": __version__,
            "migrated_from": prev,
        }, indent=2),
        encoding="utf-8",
    )
    if prev != SCHEMA_VERSION:
        actions.append(f"schema {prev}→{SCHEMA_VERSION}")
    return {
        "ok": True,
        "previous": prev,
        "current": SCHEMA_VERSION,
        "actions": actions,
        "data_dir": str(data_dir),
    }


def integrity_report(data_dir: Path) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    issues = []
    marker = data_dir / ".pear_schema.json"
    if not marker.exists():
        issues.append("missing schema marker (run migrate)")
    else:
        try:
            meta = json.loads(marker.read_text(encoding="utf-8"))
            if int(meta.get("schema_version") or 0) < SCHEMA_VERSION:
                issues.append("schema behind; migrate recommended")
        except Exception as e:
            issues.append(f"schema unreadable: {e}")
    return {"ok": len(issues) == 0, "issues": issues, "version": __version__, "schema": SCHEMA_VERSION}
