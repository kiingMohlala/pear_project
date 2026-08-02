"""Plugin manifest schema (v1.10)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


PEAR_VERSION = "1.10"


@dataclass
class PluginManifest:
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    entry: str = "plugin.py"  # relative module file
    capabilities: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    pear_version: str = ">=1.0"
    dependencies: List[str] = field(default_factory=list)  # other plugin names
    checksum: str = ""  # sha256 of entry file
    enabled_by_default: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def load(cls, path: Path) -> "PluginManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def version_compatible(requirement: str, current: str = PEAR_VERSION) -> bool:
    """Minimal semver check: >=X.Y, ==X.Y, or bare X.Y."""
    req = (requirement or "").strip()
    if not req:
        return True
    cur = _parse(current)
    if req.startswith(">="):
        return cur >= _parse(req[2:])
    if req.startswith("=="):
        return cur == _parse(req[2:])
    if req.startswith(">"):
        return cur > _parse(req[1:])
    return cur >= _parse(req)


def _parse(v: str) -> tuple:
    parts = []
    for p in v.strip().split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])
