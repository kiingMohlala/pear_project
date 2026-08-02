"""
PluginManager – discover, verify, load, enable/disable plugins.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .manifest import PluginManifest, version_compatible, PEAR_VERSION
from .api import PluginAPI
from .base import Plugin

if TYPE_CHECKING:
    from ..orchestrator import Orchestrator


@dataclass
class PluginRecord:
    manifest: PluginManifest
    path: Path
    enabled: bool = False
    loaded: bool = False
    instance: Optional[Plugin] = None
    error: Optional[str] = None
    checksum_ok: bool = True


class PluginManager:
    def __init__(
        self,
        orch: "Orchestrator",
        plugins_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        self.orch = orch
        root = Path(__file__).resolve().parents[2]  # PEAR/
        self.plugins_dir = Path(plugins_dir) if plugins_dir else root / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = Path(state_path) if state_path else self.plugins_dir / ".state.json"
        self.plugins: Dict[str, PluginRecord] = {}
        self._state: Dict[str, Any] = {"enabled": {}, "disabled": []}
        self._load_state()

    # ── discovery ─────────────────────────────────────────────────

    def discover(self) -> List[PluginRecord]:
        found: List[PluginRecord] = []
        for child in sorted(self.plugins_dir.iterdir() if self.plugins_dir.exists() else []):
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest_path = child / "plugin.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = PluginManifest.load(manifest_path)
            except Exception as e:
                rec = PluginRecord(
                    manifest=PluginManifest(name=child.name),
                    path=child,
                    error=f"Invalid manifest: {e}",
                    checksum_ok=False,
                )
                self.plugins[child.name] = rec
                found.append(rec)
                continue
            entry = child / manifest.entry
            checksum_ok = self._verify_checksum(entry, manifest.checksum)
            rec = PluginRecord(
                manifest=manifest,
                path=child,
                checksum_ok=checksum_ok,
                enabled=self._state.get("enabled", {}).get(manifest.name, manifest.enabled_by_default),
            )
            if not checksum_ok and manifest.checksum:
                rec.error = "Checksum mismatch — plugin may be tampered"
            self.plugins[manifest.name] = rec
            found.append(rec)
        return found

    def _verify_checksum(self, entry: Path, expected: str) -> bool:
        if not expected:
            return True  # unsigned plugins allowed in dev
        if not entry.exists():
            return False
        h = hashlib.sha256(entry.read_bytes()).hexdigest()
        return h == expected.lower()

    def compute_checksum(self, entry: Path) -> str:
        return hashlib.sha256(Path(entry).read_bytes()).hexdigest()

    # ── lifecycle ─────────────────────────────────────────────────

    def load_all(self) -> Dict[str, str]:
        """Discover, resolve deps, load enabled plugins. Returns name→status."""
        self.discover()
        order = self._resolve_order()
        results = {}
        for name in order:
            rec = self.plugins[name]
            if not rec.enabled:
                results[name] = "disabled"
                continue
            if not version_compatible(rec.manifest.pear_version):
                rec.error = f"Incompatible with PEAR {PEAR_VERSION}"
                results[name] = f"incompatible: {rec.error}"
                continue
            if rec.manifest.checksum and not rec.checksum_ok:
                results[name] = f"checksum_failed: {rec.error}"
                continue
            try:
                self._load_one(rec)
                results[name] = "loaded"
            except Exception as e:
                rec.error = str(e)
                results[name] = f"error: {e}"
        return results

    def _load_one(self, rec: PluginRecord) -> None:
        try:
            from ..tracing import get_tracer
            span_cm = get_tracer().span(
                f"plugin.load.{rec.manifest.name}",
                kind="internal",
                plugin=rec.manifest.name,
            )
        except Exception:
            from contextlib import nullcontext
            span_cm = nullcontext()

        with span_cm:
            entry = rec.path / rec.manifest.entry
            if not entry.exists():
                raise FileNotFoundError(f"Entry not found: {entry}")
            mod_name = f"pear_plugin_{rec.manifest.name}"
            spec = importlib.util.spec_from_file_location(mod_name, entry)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load {entry}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            plugin_cls = getattr(module, "PluginImpl", None) or getattr(module, "Plugin", None)
            if plugin_cls is None:
                raise TypeError("Plugin module must export PluginImpl or Plugin class")
            instance: Plugin = plugin_cls()
            instance.manifest = rec.manifest
            api = PluginAPI(self.orch, rec.manifest)
            instance.install(api)
            instance.load(api)
            instance.enable(api)
            rec.instance = instance
            rec.loaded = True
            rec.enabled = True
            self._emit("plugin_loaded", rec.manifest.name)

    def enable(self, name: str) -> str:
        rec = self._require(name)
        rec.enabled = True
        self._state.setdefault("enabled", {})[name] = True
        if name in self._state.get("disabled", []):
            self._state["disabled"].remove(name)
        self._save_state()
        if not rec.loaded:
            self._load_one(rec)
        elif rec.instance:
            api = PluginAPI(self.orch, rec.manifest)
            rec.instance.enable(api)
        return f"enabled {name}"

    def disable(self, name: str) -> str:
        rec = self._require(name)
        if rec.instance:
            api = PluginAPI(self.orch, rec.manifest)
            rec.instance.disable(api)
        rec.enabled = False
        self._state.setdefault("enabled", {})[name] = False
        self._state.setdefault("disabled", [])
        if name not in self._state["disabled"]:
            self._state["disabled"].append(name)
        self._save_state()
        self._emit("plugin_disabled", name)
        return f"disabled {name}"

    def uninstall(self, name: str) -> str:
        rec = self._require(name)
        if rec.instance:
            api = PluginAPI(self.orch, rec.manifest)
            try:
                rec.instance.uninstall(api)
            except Exception:
                pass
        rec.enabled = False
        rec.loaded = False
        # remove state; do not delete files automatically for safety
        self._state.get("enabled", {}).pop(name, None)
        self._save_state()
        return f"uninstalled {name} (files retained at {rec.path})"

    def info(self, name: str) -> Dict[str, Any]:
        rec = self._require(name)
        return {
            "name": rec.manifest.name,
            "version": rec.manifest.version,
            "author": rec.manifest.author,
            "description": rec.manifest.description,
            "enabled": rec.enabled,
            "loaded": rec.loaded,
            "checksum_ok": rec.checksum_ok,
            "error": rec.error,
            "permissions": rec.manifest.permissions,
            "capabilities": rec.manifest.capabilities,
            "dependencies": rec.manifest.dependencies,
            "pear_version": rec.manifest.pear_version,
            "path": str(rec.path),
        }

    def list_plugins(self) -> List[Dict[str, Any]]:
        if not self.plugins:
            self.discover()
        return [
            {
                "name": r.manifest.name,
                "version": r.manifest.version,
                "enabled": r.enabled,
                "loaded": r.loaded,
                "error": r.error,
            }
            for r in self.plugins.values()
        ]

    # ── deps ──────────────────────────────────────────────────────

    def _resolve_order(self) -> List[str]:
        """Topological sort by dependencies; skip cycles."""
        names = list(self.plugins.keys())
        deps = {n: list(self.plugins[n].manifest.dependencies or []) for n in names}
        resolved: List[str] = []
        pending = set(names)
        while pending:
            progress = False
            for n in list(pending):
                if all(d in resolved or d not in self.plugins for d in deps[n]):
                    resolved.append(n)
                    pending.remove(n)
                    progress = True
            if not progress:
                # cycle or missing — append remaining
                resolved.extend(sorted(pending))
                break
        return resolved

    # ── state ─────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                self._state = {"enabled": {}, "disabled": []}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def _require(self, name: str) -> PluginRecord:
        if name not in self.plugins:
            self.discover()
        if name not in self.plugins:
            raise KeyError(f"Unknown plugin: {name}")
        return self.plugins[name]

    def _emit(self, kind: str, name: str) -> None:
        try:
            from ..events import EventType
            self.orch.events.emit(
                EventType.NOTE,
                {"kind": kind, "plugin": name},
                source="plugin_manager",
            )
        except Exception:
            pass
