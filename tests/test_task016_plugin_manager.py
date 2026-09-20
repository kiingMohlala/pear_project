"""
PEAR 3.2 Task 016 — PluginManager concurrent persistence remediation.

Root cause, established in Phase 1 before writing any fix: PluginManager
is NOT the same ownership shape as Calendar/Memory/Learning/
SelfImprovement, despite resembling them at the code level (also a bare
write_text() on a shared instance). Those four are genuinely
one-instance-per-file (each user's own persist_dir). PluginManager's
state_path defaults to a repo-relative path with no per-user override
anywhere in the codebase (confirmed: `Orchestrator.__init__` always
constructs `PluginManager(self)` with no plugins_dir/state_path
argument) -- so every user's Orchestrator builds its OWN PluginManager
instance, but every one of those instances points at the SAME file.
That's Gate 12's Quant failure shape (independently-constructed
instances sharing one file, each caching a private snapshot), not Task
013/015's shape (one instance, no staleness possible). A lock alone --
proven insufficient here with a direct counter-test during
investigation (0/10 trials clean, every single one lost data) -- isn't
enough; the fix reloads state fresh from disk *inside* the lock before
applying each mutation.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plugins.manager import PluginManager
from core.plugins.manifest import PluginManifest


class _FakeOrch:
    def __init__(self):
        self.memory = type("M", (), {"persist_dir": None})()


def _make_pm(td: Path) -> PluginManager:
    return PluginManager(
        _FakeOrch(),
        plugins_dir=Path(td) / "plugins",
        state_path=Path(td) / "plugins" / ".state.json",
    )


# ── Direct persistence-layer races (mirrors Task 013/015 style) ─────

def test_concurrent_state_saves_no_corruption_single_instance():
    """Baseline: one shared instance, N threads mutating+saving
    concurrently via the real internal path (_locked_mutate_state).
    Pre-fix equivalent already reproduced in Task 015 (3/10 trials
    corrupted); post-fix must be clean across repeated trials."""
    N = 50
    for _trial in range(10):
        with tempfile.TemporaryDirectory() as td:
            pm = _make_pm(td)
            errors = []

            def worker(i):
                try:
                    pm._locked_mutate_state(
                        lambda i=i: pm._state.setdefault("enabled", {}).__setitem__(f"plugin-{i}", True)
                    )
                except Exception as e:
                    errors.append((i, repr(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            assert not [t for t in threads if t.is_alive()], "possible deadlock"
            assert not errors, f"unexpected errors: {errors}"

            data = json.loads(pm.state_path.read_text(encoding="utf-8"))
            assert len(data.get("enabled", {})) == N


def test_concurrent_state_saves_no_loss_multiple_instances_same_file():
    """The scenario that actually matters for PluginManager and that a
    Calendar-style lock-only fix would NOT catch: many independently-
    constructed PluginManager instances (one per simulated user session,
    exactly how Orchestrator.__init__ builds one per user with no
    override) all pointed at the SAME state_path. Each instance loads
    its own private snapshot at construction; without reload-before-
    write, a stale instance's save can silently clobber a fresher one
    from a different instance. Confirmed during investigation: a
    lock-only counterpart to this exact test lost data in 10/10 trials
    (0/10 clean). This test's target is the fixed, reload-before-write
    behavior."""
    N = 30
    for _trial in range(10):
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "plugins" / ".state.json"
            errors = []

            def worker(i):
                try:
                    pm = PluginManager(_FakeOrch(), plugins_dir=Path(td) / "plugins", state_path=state_path)
                    pm._locked_mutate_state(
                        lambda i=i, pm=pm: pm._state.setdefault("enabled", {}).__setitem__(f"plugin-{i}", True)
                    )
                except Exception as e:
                    errors.append((i, repr(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            assert not [t for t in threads if t.is_alive()], "possible deadlock"
            assert not errors, f"unexpected errors: {errors}"

            data = json.loads(state_path.read_text(encoding="utf-8"))
            assert len(data.get("enabled", {})) == N, (
                "lost writes across independently-constructed instances sharing "
                "one file -- this is exactly the scenario a lock-only fix "
                "(proven insufficient during investigation) would still allow"
            )


# ── Real enable/disable/uninstall lifecycle under concurrency ───────

def _plugin_dir_with_manifest(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(json.dumps({
        "name": name, "version": "1.0", "author": "t", "description": "",
        "entry": "plugin.py", "permissions": [], "capabilities": [], "dependencies": [],
    }))
    (d / "plugin.py").write_text(
        "from core.plugins.base import Plugin\n"
        "class PluginImpl(Plugin):\n"
        "    def load(self, api): pass\n"
    )
    return d


def test_concurrent_enable_disable_same_plugin_stays_consistent():
    """Real enable()/disable() calls (not just the internal persistence
    primitive), hammered concurrently on ONE shared instance for the
    SAME plugin name -- JSON must stay valid and the final state.json
    must always reflect a value actually chosen by some call, never a
    torn file."""
    with tempfile.TemporaryDirectory() as td:
        plugins_root = Path(td) / "plugins"
        _plugin_dir_with_manifest(plugins_root, "toggle_me")
        pm = _make_pm(td)
        pm.discover()
        errors = []

        def worker(i):
            try:
                if i % 2 == 0:
                    pm.enable("toggle_me")
                else:
                    pm.disable("toggle_me")
            except Exception as e:
                errors.append((i, repr(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not [t for t in threads if t.is_alive()], "possible deadlock"
        assert not errors, f"unexpected errors: {errors}"

        data = json.loads(pm.state_path.read_text(encoding="utf-8"))
        assert data["enabled"]["toggle_me"] in (True, False)  # valid JSON, a real decided value


def test_lock_released_after_write_exception():
    """Exception-safety: atomic_write_text raising mid-save must not
    leave the lock held for a later, unrelated call."""
    with tempfile.TemporaryDirectory() as td:
        pm = _make_pm(td)

        with patch("core.security.atomic_write_text", side_effect=RuntimeError("disk full")):
            with pytest.raises(RuntimeError):
                pm._locked_mutate_state(lambda: pm._state.setdefault("enabled", {}).__setitem__("x", True))

        done = threading.Event()

        def try_again():
            pm._locked_mutate_state(lambda: pm._state.setdefault("enabled", {}).__setitem__("y", True))
            done.set()

        t = threading.Thread(target=try_again)
        t.start()
        t.join(timeout=5)
        assert done.is_set(), "lock was not released after an exception during save"

        data = json.loads(pm.state_path.read_text(encoding="utf-8"))
        assert data["enabled"].get("y") is True
        # Note: this does NOT assert "x" is absent -- the mutate callback
        # mutates the live self._state dict before the write is attempted,
        # so an in-memory trace of the failed mutation is expected and
        # harmless (it was never persisted; the next successful save just
        # persists it too, same as any other in-memory change made
        # earlier in the object's life). What actually matters --
        # covered above -- is that the write failure doesn't leave the
        # lock held.


def test_state_path_stays_scoped_when_explicitly_overridden():
    """Preserves existing per-instance path-override behavior (this
    task did not change scoping, only concurrency safety): two
    instances given distinct state_paths never see each other's state."""
    with tempfile.TemporaryDirectory() as td:
        pm_a = PluginManager(_FakeOrch(), plugins_dir=Path(td) / "a", state_path=Path(td) / "a" / ".state.json")
        pm_b = PluginManager(_FakeOrch(), plugins_dir=Path(td) / "b", state_path=Path(td) / "b" / ".state.json")

        pm_a._locked_mutate_state(lambda: pm_a._state.setdefault("enabled", {}).__setitem__("only_in_a", True))
        pm_b._locked_mutate_state(lambda: pm_b._state.setdefault("enabled", {}).__setitem__("only_in_b", True))

        data_a = json.loads(pm_a.state_path.read_text(encoding="utf-8"))
        data_b = json.loads(pm_b.state_path.read_text(encoding="utf-8"))
        assert "only_in_a" in data_a["enabled"] and "only_in_a" not in data_b["enabled"]
        assert "only_in_b" in data_b["enabled"] and "only_in_b" not in data_a["enabled"]


def test_repeated_operations_stay_stable():
    """Locking behavior under repeated sequential operations (not just
    one concurrent burst) -- confirms no lock leak or state drift across
    many cycles."""
    with tempfile.TemporaryDirectory() as td:
        pm = _make_pm(td)
        for i in range(200):
            pm._locked_mutate_state(lambda i=i: pm._state.setdefault("enabled", {}).__setitem__(f"p{i}", True))
        data = json.loads(pm.state_path.read_text(encoding="utf-8"))
        assert len(data["enabled"]) == 200
