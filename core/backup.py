"""Backup and restore utilities (v2.40)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def zf_names_excluding_manifest(backup_path: Path) -> List[str]:
    with zipfile.ZipFile(backup_path, "r") as zf:
        return [n for n in zf.namelist() if n != "MANIFEST.json"]


def _unsafe_member_reason(name: str) -> Optional[str]:
    """
    First-line string filter for an archive member name, ahead of the
    authoritative resolve()-based containment check in restore() --
    this alone is not the security boundary (a name can look "safe" as
    a string and still resolve outside target through a pre-existing
    symlink in the destination tree), but it gives a clear, specific
    rejection reason for the common attack shapes rather than a bare
    "resolves outside target" for everything.
    """
    if not name:
        return "empty entry name"
    if "\x00" in name:
        return "embedded NUL byte"
    if "\\" in name:
        return "backslash in entry name (Windows-style separator/traversal)"
    if ":" in name:
        return "colon in entry name (drive-qualified path)"
    if name.startswith("/"):
        return "absolute path"
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return "'..' path traversal component"
    return None


class BackupManager:
    def __init__(self, data_dir: Path, backup_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self.backup_dir = Path(backup_dir) if backup_dir else self.data_dir.parent / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create(self, label: str = "manual") -> Dict[str, Any]:
        ts = time.strftime("%Y%m%d_%H%M%S")
        name = f"pear_backup_{label}_{ts}.zip"
        dest = self.backup_dir / name
        include = [
            "goals",
            "learning",
            "workers",
            "sessions",
            "audit.jsonl",
            "users.json",
            "config.json",
        ]
        # also any session json memory dumps
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if self.data_dir.exists():
                for path in self.data_dir.rglob("*"):
                    if path.is_file():
                        rel = path.relative_to(self.data_dir)
                        # skip huge caches
                        if any(p in rel.parts for p in ("__pycache__", ".git")):
                            continue
                        zf.write(path, arcname=str(rel))
            manifest = {
                "created_at": time.time(),
                "label": label,
                "data_dir": str(self.data_dir),
                "files": zf.namelist(),
            }
            zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
        checksum = _sha256_file(dest)
        meta = {
            "path": str(dest),
            "checksum": checksum,
            "size": dest.stat().st_size,
            "label": label,
            "created_at": time.time(),
        }
        (dest.with_suffix(dest.suffix + ".sha256")).write_text(checksum, encoding="utf-8")
        return meta

    def list_backups(self) -> List[Dict[str, Any]]:
        out = []
        for p in sorted(self.backup_dir.glob("pear_backup_*.zip"), reverse=True):
            out.append({
                "path": str(p),
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
                "checksum_file": str(p.with_suffix(p.suffix + ".sha256")),
            })
        return out

    def verify(self, backup_path: Path) -> Dict[str, Any]:
        backup_path = Path(backup_path)
        if not backup_path.exists():
            return {"ok": False, "error": "missing backup"}
        actual = _sha256_file(backup_path)
        expected = None
        side = backup_path.with_suffix(backup_path.suffix + ".sha256")
        if side.exists():
            expected = side.read_text(encoding="utf-8").strip()
        ok = expected is None or expected == actual
        # integrity of zip
        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                bad = zf.testzip()
                names = zf.namelist()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": ok and bad is None,
            "checksum": actual,
            "expected": expected,
            "bad_entry": bad,
            "files": len(names),
        }

    def restore(self, backup_path: Path, target_dir: Optional[Path] = None, *, dry_run: bool = False) -> Dict[str, Any]:
        backup_path = Path(backup_path)
        target = Path(target_dir) if target_dir else self.data_dir
        ver = self.verify(backup_path)
        if not ver.get("ok"):
            return {"ok": False, "error": "verify failed", "verify": ver}
        if dry_run:
            return {"ok": True, "dry_run": True, "target": str(target), "verify": ver}
        target.mkdir(parents=True, exist_ok=True)
        # PEAR 3.2 Task 017: destinations were previously built as
        # `target / name` straight from zf.namelist(), with no
        # containment check at all -- a crafted entry like
        # "../outside_marker.txt" would extract outside `target`
        # (reproduced independently before this fix: a real file was
        # written outside a sandboxed target directory this way).
        # Every entry is now validated *before any extraction begins*
        # (Phase 1: "avoid partially extracting unsafe archives" --
        # the whole restore is rejected up front rather than silently
        # skipping unsafe members mixed in with safe ones, which would
        # otherwise leave the caller unsure how much of the backup
        # actually landed). Containment is checked by resolving the
        # real, symlink-following filesystem path of each computed
        # destination and requiring it stay inside the resolved target
        # root -- not by string-prefix comparison on the unresolved
        # path, which a symlink already present in the target tree (not
        # necessarily one created by this archive -- this
        # implementation only ever copies file bytes, never recreates
        # an actual symlink from a zip entry, so a *malicious entry*
        # can't plant one for a later entry to walk through, but a
        # *pre-existing* symlink under `target` from anywhere else
        # could still redirect an otherwise-innocent-looking entry) or
        # a "safe-looking" `..`-free string could still bypass.
        target_root = target.resolve()
        safe_members: List[tuple] = []  # (name, resolved_dest, is_dir_marker)
        rejected: List[Dict[str, str]] = []
        for name in zf_names_excluding_manifest(backup_path):
            reason = _unsafe_member_reason(name)
            if reason:
                rejected.append({"name": name, "reason": reason})
                continue
            is_dir_marker = name.endswith("/")
            dest = target / name
            try:
                resolved = dest.resolve()
            except (OSError, RuntimeError) as e:
                rejected.append({"name": name, "reason": f"could not resolve destination: {e}"})
                continue
            if resolved != target_root and target_root not in resolved.parents:
                rejected.append({"name": name, "reason": "resolves outside target directory"})
                continue
            safe_members.append((name, resolved, is_dir_marker))

        if rejected:
            return {
                "ok": False,
                "error": "unsafe archive: rejected before any extraction",
                "target": str(target),
                "verify": ver,
                "rejected_entries": rejected,
            }

        with zipfile.ZipFile(backup_path, "r") as zf:
            for name, resolved, is_dir_marker in safe_members:
                if is_dir_marker:
                    resolved.mkdir(parents=True, exist_ok=True)
                    continue
                resolved.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, resolved.open("wb") as out:
                    shutil.copyfileobj(src, out)
        return {"ok": True, "target": str(target), "verify": ver}
