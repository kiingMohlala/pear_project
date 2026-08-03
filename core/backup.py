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
        with zipfile.ZipFile(backup_path, "r") as zf:
            for name in zf.namelist():
                if name == "MANIFEST.json":
                    continue
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
        return {"ok": True, "target": str(target), "verify": ver}
