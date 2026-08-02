"""
Secure desktop automation helpers (v0.70).

Workspace sandboxing, permission groups, and safe file operations.
Destructive ops prefer trash; paths outside workspace require approval.
"""

from __future__ import annotations

import os
import platform
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Permission groups
PERM_GROUPS: Dict[str, Set[str]] = {
    "desktop_read": {
        "list_directory",
        "search_files",
        "get_system_info",
        "open_folder",
    },
    "desktop_write": {
        "copy_file",
        "move_file",
        "rename_file",
        "create_folder",
    },
    "desktop_delete": {
        "delete_file",
    },
    "desktop_launch": {
        "open_application",
    },
    "desktop_capture": {
        "take_screenshot",
    },
}

ALL_DESKTOP_PERMS = set()
for _g in PERM_GROUPS.values():
    ALL_DESKTOP_PERMS |= _g


class Workspace:
    """
    Restricts automation to approved roots.
    Default: ~/PEAR_Workspace (created if missing) + optional extras.
    """

    def __init__(self, roots: Optional[List[Path]] = None):
        default = Path.home() / "PEAR_Workspace"
        try:
            default.mkdir(parents=True, exist_ok=True)
        except OSError:
            default = Path.cwd() / "PEAR_Workspace"
            default.mkdir(parents=True, exist_ok=True)
        self.roots: List[Path] = []
        for r in roots or [default]:
            self.add_root(r)

    def add_root(self, path: Path | str) -> Path:
        p = Path(path).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        if p not in self.roots:
            self.roots.append(p)
        return p

    def remove_root(self, path: Path | str) -> None:
        p = Path(path).expanduser().resolve()
        self.roots = [r for r in self.roots if r != p]

    def list_roots(self) -> List[str]:
        return [str(r) for r in self.roots]

    def resolve(self, path: Path | str) -> Path:
        return Path(path).expanduser().resolve()

    def is_inside(self, path: Path | str) -> bool:
        p = self.resolve(path)
        for root in self.roots:
            try:
                p.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def require_inside(self, path: Path | str) -> Path:
        p = self.resolve(path)
        if not self.is_inside(p):
            raise PermissionError(
                f"Path outside workspace sandbox: {p}. "
                f"Approved roots: {self.list_roots()}"
            )
        return p


# ── safe operations ───────────────────────────────────────────────

def list_directory(path: str | Path, *, workspace: Optional[Workspace] = None) -> Dict[str, Any]:
    p = Path(path).expanduser()
    if workspace:
        p = workspace.require_inside(p)
    else:
        p = p.resolve()
    if not p.exists():
        return {"ok": False, "error": f"Not found: {p}"}
    if not p.is_dir():
        return {"ok": False, "error": f"Not a directory: {p}"}
    entries = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": str(child),
                "type": "dir" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
            })
        except OSError:
            entries.append({"name": child.name, "path": str(child), "type": "unknown"})
    return {"ok": True, "path": str(p), "entries": entries, "count": len(entries)}


def copy_file(
    src: str | Path,
    dest: str | Path,
    *,
    workspace: Optional[Workspace] = None,
    allow_outside: bool = False,
) -> Dict[str, Any]:
    s = Path(src).expanduser().resolve()
    d = Path(dest).expanduser().resolve()
    if workspace:
        if not allow_outside:
            workspace.require_inside(s)
            workspace.require_inside(d if d.suffix or d.exists() else d.parent)
        elif not workspace.is_inside(s) or not workspace.is_inside(d.parent if not d.exists() else d):
            return {
                "ok": False,
                "error": "outside_workspace",
                "needs_approval": True,
                "message": f"Copy involves paths outside workspace: {s} → {d}",
            }
    if not s.exists():
        return {"ok": False, "error": f"Source not found: {s}"}
    if d.exists() and d.is_dir():
        d = d / s.name
    if d.exists():
        return {
            "ok": False,
            "error": "destination_exists",
            "needs_approval": True,
            "message": f"Overwrite required for {d}",
            "dest": str(d),
        }
    d.parent.mkdir(parents=True, exist_ok=True)
    if s.is_dir():
        shutil.copytree(s, d)
    else:
        shutil.copy2(s, d)
    return {"ok": True, "src": str(s), "dest": str(d), "message": f"Copied to {d}"}


def move_file(
    src: str | Path,
    dest: str | Path,
    *,
    workspace: Optional[Workspace] = None,
    allow_outside: bool = False,
) -> Dict[str, Any]:
    s = Path(src).expanduser().resolve()
    d = Path(dest).expanduser().resolve()
    if workspace:
        outside = (not workspace.is_inside(s)) or (
            not workspace.is_inside(d if d.exists() else d.parent)
        )
        if outside and not allow_outside:
            return {
                "ok": False,
                "error": "outside_workspace",
                "needs_approval": True,
                "message": f"Move outside workspace requires approval: {s} → {d}",
            }
        if not outside:
            workspace.require_inside(s)
    if not s.exists():
        return {"ok": False, "error": f"Source not found: {s}"}
    if d.exists() and d.is_dir():
        d = d / s.name
    if d.exists():
        return {
            "ok": False,
            "error": "destination_exists",
            "needs_approval": True,
            "message": f"Move would overwrite {d}",
        }
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"ok": True, "src": str(s), "dest": str(d), "message": f"Moved to {d}"}


def rename_file(
    path: str | Path,
    new_name: str,
    *,
    workspace: Optional[Workspace] = None,
) -> Dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if workspace:
        workspace.require_inside(p)
    if not p.exists():
        return {"ok": False, "error": f"Not found: {p}"}
    dest = p.parent / new_name
    if dest.exists():
        return {"ok": False, "error": f"Already exists: {dest}", "needs_approval": True}
    p.rename(dest)
    return {"ok": True, "src": str(p), "dest": str(dest), "message": f"Renamed to {dest.name}"}


def delete_file(
    path: str | Path,
    *,
    workspace: Optional[Workspace] = None,
    use_trash: bool = True,
) -> Dict[str, Any]:
    """Delete by moving to workspace .trash (never permanent unless use_trash=False + approved)."""
    p = Path(path).expanduser().resolve()
    if workspace:
        workspace.require_inside(p)
    if not p.exists():
        return {"ok": False, "error": f"Not found: {p}"}
    if use_trash:
        trash_root = (workspace.roots[0] if workspace and workspace.roots else Path.home() / "PEAR_Workspace") / ".trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        dest = trash_root / f"{int(time.time())}_{p.name}"
        shutil.move(str(p), str(dest))
        return {
            "ok": True,
            "path": str(p),
            "trash": str(dest),
            "message": f"Moved to trash: {dest}",
        }
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"ok": True, "path": str(p), "message": f"Permanently deleted {p}"}


def create_folder(
    path: str | Path,
    *,
    workspace: Optional[Workspace] = None,
) -> Dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if workspace:
        # allow creating under workspace root
        if not workspace.is_inside(p) and not workspace.is_inside(p.parent):
            workspace.require_inside(p)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(p), "message": f"Created folder {p}"}


def get_system_info() -> Dict[str, Any]:
    return {
        "ok": True,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "cwd": str(Path.cwd()),
        "home": str(Path.home()),
    }


def take_screenshot(
    dest: Optional[str | Path] = None,
    *,
    workspace: Optional[Workspace] = None,
) -> Dict[str, Any]:
    """
    Best-effort screenshot. Saves under workspace if possible.
    Returns path or clear error if unsupported in environment.
    """
    if dest is None:
        root = workspace.roots[0] if workspace and workspace.roots else Path.home() / "PEAR_Workspace"
        dest = root / f"screenshot_{uuid.uuid4().hex[:8]}.png"
    dest_p = Path(dest).expanduser().resolve()
    if workspace and not workspace.is_inside(dest_p):
        dest_p = workspace.roots[0] / dest_p.name
    dest_p.parent.mkdir(parents=True, exist_ok=True)

    system = platform.system().lower()
    try:
        if system == "linux":
            # try import-free tools
            for cmd in (
                ["gnome-screenshot", "-f", str(dest_p)],
                ["scrot", str(dest_p)],
                ["import", "-window", "root", str(dest_p)],
            ):
                try:
                    import subprocess
                    r = subprocess.run(cmd, capture_output=True, timeout=10)
                    if r.returncode == 0 and dest_p.exists():
                        return {"ok": True, "path": str(dest_p), "message": f"Screenshot saved to {dest_p}"}
                except Exception:
                    continue
            return {"ok": False, "error": "No screenshot utility available (gnome-screenshot/scrot/ImageMagick)"}
        if system == "darwin":
            import subprocess
            r = subprocess.run(["screencapture", "-x", str(dest_p)], capture_output=True, timeout=10)
            if r.returncode == 0 and dest_p.exists():
                return {"ok": True, "path": str(dest_p), "message": f"Screenshot saved to {dest_p}"}
            return {"ok": False, "error": "screencapture failed"}
        if system == "windows":
            return {"ok": False, "error": "Screenshot on Windows not implemented in this build"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "Screenshot unsupported on this platform"}
