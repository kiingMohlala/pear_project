"""
PEAR 3.2 Task 015 — Calendar persistence remediation + PluginManager
investigation.

Calendar: fixed. Same ownership/lifecycle shape as Memory/Learning/
SelfImprovement (one CalendarConnector instance built per Orchestrator,
reused across that user's own requests, single in-memory _events list,
never reconstructed mid-session) -- confirmed by inspection before
reusing Task 013's locking pattern, not copied blindly.

PluginManager: investigated and reproduced (CONFIRMED_BUG, same failure
mode -- torn/corrupted JSON under concurrent _save_state() calls), but
NOT fixed in this task -- Task 015's scope authorized Calendar
remediation only. The xfail test below documents the confirmed bug as a
permanent regression marker: it will report XPASS (a signal, not a
silent pass) if a future task fixes PluginManager without updating this
file.
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

from core.connectors.calendar_connector import CalendarConnector
from core.plugins.manager import PluginManager


# ── Calendar: concurrent event creation ─────────────────────────────

def test_calendar_concurrent_create_no_loss_no_corruption():
    """Direct reproduction, same shape as the Memory/Learning/
    SelfImprovement races Task 013 fixed. Pre-fix: reproducibly lost
    events and/or corrupted JSON under concurrent create_event calls on
    one shared CalendarConnector instance (confirmed this session: 3/10
    trials failed pre-fix). Post-fix: every event survives, file always
    valid JSON."""
    N = 50
    for _trial in range(10):
        with tempfile.TemporaryDirectory() as td:
            cal = CalendarConnector(store_path=Path(td) / "calendar.json")
            cal.connect()
            errors = []

            def worker(i):
                try:
                    r = cal.execute("create_event", title=f"evt-{i}")
                    assert r.ok
                except Exception as e:
                    errors.append((i, repr(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            stuck = [t for t in threads if t.is_alive()]
            assert not stuck, f"{len(stuck)} threads did not complete -- possible deadlock"
            assert not errors, f"unexpected errors: {errors}"

            raw = cal.store_path.read_text(encoding="utf-8")
            data = json.loads(raw)  # raises on torn/corrupt JSON
            assert len(data) == len(cal._events) == N


def test_calendar_concurrent_delete_no_loss_no_corruption():
    """Same race, delete path: pre-populate N events, then concurrently
    delete half of them from multiple threads."""
    N = 40
    with tempfile.TemporaryDirectory() as td:
        cal = CalendarConnector(store_path=Path(td) / "calendar.json")
        cal.connect()
        ids = []
        for i in range(N):
            r = cal.execute("create_event", title=f"evt-{i}")
            ids.append(r.data["id"])

        to_delete = ids[: N // 2]
        errors = []

        def worker(eid):
            try:
                r = cal.execute("delete_event", id=eid)
                assert r.ok
            except Exception as e:
                errors.append((eid, repr(e)))

        threads = [threading.Thread(target=worker, args=(eid,)) for eid in to_delete]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not [t for t in threads if t.is_alive()], "possible deadlock"
        assert not errors, f"unexpected errors: {errors}"

        data = json.loads(cal.store_path.read_text(encoding="utf-8"))
        remaining_ids = {e["id"] for e in data}
        assert remaining_ids == set(ids[N // 2:])
        assert len(data) == len(cal._events) == N - len(to_delete)


def test_calendar_lock_released_after_write_exception():
    """Exception-safety: if atomic_write_text raises mid-save, the lock
    must still be released -- a later, unrelated call must not hang."""
    with tempfile.TemporaryDirectory() as td:
        cal = CalendarConnector(store_path=Path(td) / "calendar.json")
        cal.connect()

        with patch("core.security.atomic_write_text", side_effect=RuntimeError("disk full")):
            with pytest.raises(RuntimeError):
                cal.execute("create_event", title="will-fail")

        # lock must not still be held -- this must complete promptly,
        # not hang, and must succeed normally
        done = threading.Event()

        def try_again():
            cal.execute("create_event", title="recovers-fine")
            done.set()

        t = threading.Thread(target=try_again)
        t.start()
        t.join(timeout=5)
        assert done.is_set(), "lock was not released after an exception during save -- deadlock risk"

        data = json.loads(cal.store_path.read_text(encoding="utf-8"))
        assert any(e["title"] == "recovers-fine" for e in data)
        assert not any(e["title"] == "will-fail" for e in data)


def test_calendar_store_path_stays_per_user():
    """Confirms the fix didn't touch scoping -- two connectors with
    distinct store_paths never see each other's events."""
    with tempfile.TemporaryDirectory() as td:
        alice = CalendarConnector(store_path=Path(td) / "alice" / "calendar.json")
        bob = CalendarConnector(store_path=Path(td) / "bob" / "calendar.json")
        alice.connect()
        bob.connect()

        alice.execute("create_event", title="ALICE_ONLY_EVENT")
        bob.execute("create_event", title="BOB_ONLY_EVENT")

        alice_titles = {e["title"] for e in alice.execute("list_events").data["events"]}
        bob_titles = {e["title"] for e in bob.execute("list_events").data["events"]}
        assert alice_titles == {"ALICE_ONLY_EVENT"}
        assert bob_titles == {"BOB_ONLY_EVENT"}
        assert str(alice.store_path) != str(bob.store_path)


# ── PluginManager: investigated, reproduced, NOT fixed this task ────

class _FakeOrch:
    def __init__(self):
        self.memory = type("M", (), {"persist_dir": None})()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PEAR 3.2 Task 015 Phase 1: PluginManager._save_state() has the "
        "same unlocked bare-write_text() race as pre-fix Calendar -- "
        "CONFIRMED_BUG by reproduction (corrupted JSON in 3/10 trials "
        "at 50 concurrent ops), but fixing it was out of this task's "
        "authorized scope (Calendar remediation only). This test is a "
        "permanent marker: it currently fails (torn/short JSON in at "
        "least one of several trials, as expected from an unlocked "
        "race) as expected. If a future task fixes PluginManager, this "
        "test will XPASS and must be updated/removed then, not before."
    ),
)
def test_pluginmanager_concurrent_state_save_currently_races():
    # The race is probabilistic (observed ~30% failure rate per trial
    # at 50 concurrent ops), so this runs several trials and requires
    # every one to be clean to "pass" -- matching the manual
    # investigation's own methodology (10 trials) rather than relying
    # on a single roll that could get lucky and mask the confirmed bug.
    N = 50
    for _trial in range(10):
        with tempfile.TemporaryDirectory() as td:
            pm = PluginManager(
                _FakeOrch(),
                plugins_dir=Path(td) / "plugins",
                state_path=Path(td) / "plugins" / ".state.json",
            )

            def worker(i):
                pm._state.setdefault("enabled", {})[f"plugin-{i}"] = True
                pm._save_state()

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            raw = pm.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)  # expected to raise on torn JSON, some trial
            assert len(data.get("enabled", {})) == N  # expected short, some trial
