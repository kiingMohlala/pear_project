"""
PEAR 3.2 Task 017 — Backup restore path-traversal remediation.

Root cause: BackupManager.restore() built extraction destinations as
`target / name` straight from zf.namelist(), with no containment check
at all. Reproduced independently before any fix: a crafted "../..."
entry wrote a real file outside a sandboxed target directory.

Reachability: restore() has exactly one caller anywhere in the live
app -- scripts/admin_cli.py's `restore` subcommand, a bare argparse
positional argument, operator-typed. Not reachable via the HTTP
service, any agent, or ui/app.py's CLI (which only wires up backup
creation, never restore).

Fix: every archive member is validated *before* any extraction begins
(reject the whole restore, not a silent partial extraction) using both
a string-level filter (clear, specific rejection reasons) and an
authoritative resolve()-based containment check (catches cases the
string filter can't, such as a pre-existing symlink already present in
the target tree redirecting an otherwise-innocent-looking entry name).
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backup import BackupManager


def _make_backup_manager(td: Path) -> BackupManager:
    data = Path(td) / "data"
    data.mkdir()
    backups = Path(td) / "backups"
    return BackupManager(data_dir=data, backup_dir=backups)


def _write_crafted_zip(path: Path, entries: dict) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


# ── Malicious archives: each must be rejected, nothing extracted ────

def test_dotdot_traversal_rejected_and_nothing_written():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        outside_marker = td / "outside.txt"
        evil = td / "evil.zip"
        _write_crafted_zip(evil, {"../outside.txt": "PWNED"})

        bm = _make_backup_manager(td)
        result = bm.restore(evil, target_dir=target)

        assert result["ok"] is False
        assert not outside_marker.exists()
        assert any("traversal" in r["reason"] for r in result["rejected_entries"])


def test_absolute_path_entry_rejected():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        # A controlled, sandboxed absolute path -- NOT a real system
        # path. This matters even for a security test: if the fix ever
        # regressed, this must still only ever write inside the temp
        # sandbox, never touch anything on the real filesystem outside
        # it (confirmed the hard way while writing this task's own
        # pre-fix verification: an earlier, less careful version of
        # this exact test targeted /etc/ and, against the *pre-fix*
        # code with no containment check, actually wrote a real file
        # there on this sandbox -- cleaned up, and this version can't
        # repeat that regardless of which code it runs against).
        absolute_target = td / "outside_via_absolute" / "evil_absolute.txt"
        evil = td / "evil_abs.zip"
        # zipfile.writestr with a leading "/" -- some zip tools strip
        # it on write, so directly manipulate the ZipInfo to force a
        # genuinely absolute-looking stored name.
        with zipfile.ZipFile(evil, "w") as zf:
            info = zipfile.ZipInfo(str(absolute_target))
            zf.writestr(info, "PWNED")

        bm = _make_backup_manager(td)
        result = bm.restore(evil, target_dir=target)

        assert result["ok"] is False
        assert not absolute_target.exists()


def test_windows_style_traversal_rejected():
    """Even on a POSIX runtime, a backslash-laden entry name must be
    rejected outright rather than treated as a literal (harmless)
    filename -- defense in depth for any future cross-platform use,
    per Phase 1's explicit requirement."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        evil = td / "evil_win.zip"
        _write_crafted_zip(evil, {"..\\..\\evil_windows.txt": "PWNED"})

        bm = _make_backup_manager(td)
        result = bm.restore(evil, target_dir=target)

        assert result["ok"] is False
        assert any("backslash" in r["reason"] for r in result["rejected_entries"])


def test_drive_qualified_path_rejected():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        evil = td / "evil_drive.zip"
        _write_crafted_zip(evil, {"C:evil_drive_qualified.txt": "PWNED"})

        bm = _make_backup_manager(td)
        result = bm.restore(evil, target_dir=target)

        assert result["ok"] is False
        assert any("colon" in r["reason"] or "drive" in r["reason"] for r in result["rejected_entries"])


def test_preexisting_symlink_in_target_cannot_be_used_to_escape():
    """The archive itself never recreates a real symlink (this
    implementation only ever copies file bytes) -- but a symlink
    already present under target, from anything else, must not let an
    otherwise-plain-looking entry name land outside target. A
    string-prefix check on the unresolved path would miss this; the
    resolve()-based containment check must catch it."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        outside = td / "outside_dir"
        outside.mkdir()
        (target / "escape_link").symlink_to(outside)

        evil = td / "evil_symlink.zip"
        _write_crafted_zip(evil, {"escape_link/evil.txt": "PWNED"})

        bm = _make_backup_manager(td)
        result = bm.restore(evil, target_dir=target)

        assert result["ok"] is False
        assert not (outside / "evil.txt").exists()


def test_one_malicious_member_among_many_safe_ones_rejects_the_whole_restore():
    """Mixed archive: mostly legitimate entries plus one traversal
    entry. Nothing from the archive should land on disk -- validated
    before any extraction begins, not skip-the-bad-one-and-continue,
    per Phase 1's requirement to avoid partial extraction of unsafe
    archives."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        evil = td / "mixed.zip"
        _write_crafted_zip(evil, {
            "goals/goal_a.json": '{"id":"a"}',
            "learning/state.json": '{"n":1}',
            "../escape.txt": "PWNED",
        })

        bm = _make_backup_manager(td)
        result = bm.restore(evil, target_dir=target)

        assert result["ok"] is False
        assert not (target / "goals" / "goal_a.json").exists(), (
            "safe entries must not be extracted when the archive as a whole is rejected"
        )
        assert not (td / "escape.txt").exists()


# ── Valid archives: existing behavior must be fully preserved ───────

def test_nested_safe_directories_still_extract_normally():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        good = td / "good.zip"
        _write_crafted_zip(good, {
            "goals/goal_x.json": '{"id":"goal_x"}',
            "learning/state.json": '{"n":1}',
            "sessions/alice/memory.json": '{"working":[]}',
        })

        bm = _make_backup_manager(td)
        result = bm.restore(good, target_dir=target)

        assert result["ok"] is True
        assert (target / "goals" / "goal_x.json").read_text() == '{"id":"goal_x"}'
        assert (target / "learning" / "state.json").exists()
        assert (target / "sessions" / "alice" / "memory.json").exists()


def test_real_created_backup_still_restores_end_to_end():
    """Full round trip through the real create() -> restore() path,
    not just a hand-crafted zip -- confirms this fix didn't change the
    normal backup/restore workflow at all."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        data = td / "data"
        data.mkdir()
        (data / "goals").mkdir()
        (data / "goals" / "goal_x.json").write_text('{"id":"goal_x"}')
        (data / "learning").mkdir()
        (data / "learning" / "learning_state.json").write_text('{"n":1}')

        bm = BackupManager(data_dir=data, backup_dir=td / "backups")
        meta = bm.create(label="t017")
        assert Path(meta["path"]).exists()

        target = td / "restored"
        result = bm.restore(Path(meta["path"]), target_dir=target)
        assert result["ok"] is True
        assert (target / "goals" / "goal_x.json").exists()
        assert (target / "learning" / "learning_state.json").exists()


def test_destination_containment_holds_after_normalization():
    """Every file that DOES get written must resolve to somewhere
    inside the resolved target root -- the actual security property
    this task defines, checked directly against the real filesystem
    result, not just against the restore() return value."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        target = td / "target"
        target.mkdir()
        good = td / "good2.zip"
        _write_crafted_zip(good, {"a/b/c/deep.txt": "fine"})

        bm = _make_backup_manager(td)
        result = bm.restore(good, target_dir=target)
        assert result["ok"] is True

        target_root = target.resolve()
        for p in target.rglob("*"):
            if p.is_file():
                assert target_root in p.resolve().parents or p.resolve() == target_root
