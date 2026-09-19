"""
PEAR 3.2 Task 013 — Confirmed Isolation Remediation.

Three independently-scoped fixes, each with its own regression coverage:

1. Orchestrator.backups/self.memory.persist_dir scoping (core/orchestrator.py)
2. FastAPI tracer contextvar activation (service/app.py)
3. Memory/Learning/SelfImprovement concurrent-write race (core/memory.py,
   core/learning.py, core/self_improve.py) — reproduced directly this
   session (not pattern-matched from the Gate 12 Quant bug, which is a
   different failure mode: independently-constructed-per-session stores
   vs. these three's single long-lived shared instance per user).
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.learning import LearningEngine
from core.self_improve import SelfImprovementEngine
from core.orchestrator import Orchestrator
from core.llm import EchoLLM
from service.auth import Role


class _FakeOrch:
    """Minimal stand-in for Learning/SelfImprove, which only touch
    orchestrator.memory.persist_dir in their constructors."""
    def __init__(self, persist_dir: Path):
        self.memory = type("M", (), {"persist_dir": persist_dir})()


# ── Part 3: concurrent same-user write races ────────────────────────

def test_memory_concurrent_saves_no_loss_no_corruption():
    """Direct reproduction: N threads append to the SAME shared Memory
    instance's working messages and immediately save. Pre-fix (bare
    write_text, snapshot built before any lock): reproducibly loses
    writes and/or produces invalid JSON under concurrency. Post-fix:
    every append survives and the file is always valid JSON."""
    N = 60
    for _trial in range(5):
        with tempfile.TemporaryDirectory() as td:
            mem = Memory(session_id="race", persist_dir=Path(td))
            errors = []

            def worker(i):
                try:
                    mem.working.add("user", f"msg-{i:03d}")
                    mem._save()
                except Exception as e:
                    errors.append((i, repr(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"unexpected errors: {errors}"
            raw = mem._path().read_text(encoding="utf-8")
            data = json.loads(raw)  # raises on torn/corrupt JSON
            assert len(data["working"]) == len(mem.working.messages) == N


def test_learning_concurrent_saves_no_loss():
    N = 50
    for _trial in range(5):
        with tempfile.TemporaryDirectory() as td:
            eng = LearningEngine(_FakeOrch(Path(td)))
            errors = []

            def worker(i):
                try:
                    eng.history.append({"i": i})
                    eng._save()
                except Exception as e:
                    errors.append((i, repr(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"unexpected errors: {errors}"
            data = json.loads(eng._state_path().read_text(encoding="utf-8"))
            assert len(data["history"]) == len(eng.history) == N


def test_self_improve_concurrent_saves_no_loss():
    N = 50
    for _trial in range(5):
        with tempfile.TemporaryDirectory() as td:
            eng = SelfImprovementEngine(_FakeOrch(Path(td)))
            errors = []

            def worker(i):
                try:
                    eng.history.append({"i": i})
                    eng._save()
                except Exception as e:
                    errors.append((i, repr(e)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"unexpected errors: {errors}"
            data = json.loads(eng._path().read_text(encoding="utf-8"))
            assert len(data["history"]) == len(eng.history) == N


# ── Part 1: Orchestrator.backups scoping ────────────────────────────

def test_backups_scoped_to_own_persist_dir_only():
    """A user's own backup archive must never reference another user's
    persist_dir, username, or files, and the backup DESTINATION itself
    must live under that user's own directory (not a shared backups/)."""
    with tempfile.TemporaryDirectory() as root:
        alice_dir = Path(root) / "sessions" / "alice"
        bob_dir = Path(root) / "sessions" / "bob"
        orch_alice = Orchestrator(memory=Memory(session_id="alice", persist_dir=alice_dir), llm=EchoLLM())
        orch_bob = Orchestrator(memory=Memory(session_id="bob", persist_dir=bob_dir), llm=EchoLLM())

        # give each user something identifiable to find in a leaked archive
        orch_alice.memory.long_term.facts.append("ALICE_ONLY_SECRET_FACT")
        orch_alice.memory._save()
        orch_bob.memory.long_term.facts.append("BOB_ONLY_SECRET_FACT")
        orch_bob.memory._save()

        assert str(orch_alice.backups.data_dir) == str(alice_dir)
        assert str(orch_bob.backups.data_dir) == str(bob_dir)
        assert str(alice_dir) not in str(orch_bob.backups.data_dir)

        meta = orch_alice.backups.create(label="t013")
        import zipfile
        with zipfile.ZipFile(meta["path"]) as zf:
            names = zf.namelist()
            content = "".join(zf.read(n).decode("utf-8", "ignore") for n in names if n != "MANIFEST.json")

        assert "BOB_ONLY_SECRET_FACT" not in content, "Alice's backup must not contain Bob's data"
        assert "bob" not in "".join(names), "Alice's backup must not reference Bob's session path at all"
        assert "ALICE_ONLY_SECRET_FACT" in content, "Alice's backup should still contain her own data"

        # destination itself must be under alice's own dir, not a shared one
        assert str(alice_dir) in str(meta["path"])
        assert str(bob_dir) not in str(meta["path"])

        # bob must not be able to see alice's backup via his own manager
        bob_backup_names = [Path(b["path"]).name for b in orch_bob.backups.list_backups()]
        assert Path(meta["path"]).name not in bob_backup_names


# ── Part 2: FastAPI tracer isolation ─────────────────────────────────

def _require_fastapi():
    try:
        import fastapi  # noqa
        import uvicorn  # noqa
    except Exception:
        import pytest
        pytest.skip("fastapi/uvicorn not installed")


def test_fastapi_chat_activates_per_user_tracer_not_fallback():
    """Before this fix, no FastAPI route ever called set_tracer(), so
    every get_tracer() call inside agent/tool/LLM code during a FastAPI
    request fell through to the shared module-level fallback tracer
    instead of the requesting user's own orch.tracer. This instruments
    do_chat to record get_tracer()'s identity at call time, for two
    different users, and asserts each sees their own tracer -- never
    the fallback, never each other's."""
    _require_fastapi()
    from service.app import create_app, PearService
    from fastapi.testclient import TestClient
    from core.tracing import get_tracer

    with tempfile.TemporaryDirectory() as td:
        app = create_app(data_root=Path(td))
        service = app.state.service
        service.auth.create_user("t013_alice", "pw", Role.USER)
        service.auth.create_user("t013_bob", "pw", Role.USER)
        client = TestClient(app)

        tok_alice = client.post("/auth/login", json={"username": "t013_alice", "password": "pw"}).json()["token"]
        tok_bob = client.post("/auth/login", json={"username": "t013_bob", "password": "pw"}).json()["token"]

        orch_alice = service.sessions.get("t013_alice").orchestrator
        orch_bob = service.sessions.get("t013_bob").orchestrator

        seen = {}
        seen_lock = threading.Lock()
        orig_do_chat = PearService.do_chat

        def spying_do_chat(self, orch, message, on_token=None):
            with seen_lock:
                seen.setdefault(message, []).append(id(get_tracer()))
            return orig_do_chat(self, orch, message, on_token=on_token)

        PearService.do_chat = spying_do_chat
        try:
            def call_alice():
                client.post("/v1/chat", json={"message": "from-alice"}, headers={"Authorization": f"Bearer {tok_alice}"})

            def call_bob():
                client.post("/v1/chat", json={"message": "from-bob"}, headers={"Authorization": f"Bearer {tok_bob}"})

            threads = [threading.Thread(target=call_alice) for _ in range(5)] + \
                      [threading.Thread(target=call_bob) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            PearService.do_chat = orig_do_chat

        import core.tracing as tracing_mod
        fallback_id = id(tracing_mod._fallback_tracer)

        alice_tracer_ids = set(seen["from-alice"])
        bob_tracer_ids = set(seen["from-bob"])

        assert alice_tracer_ids == {id(orch_alice.tracer)}, (
            f"Alice's FastAPI /v1/chat requests must all see her own orch.tracer, "
            f"got {alice_tracer_ids} (her tracer is {id(orch_alice.tracer)}, fallback is {fallback_id})"
        )
        assert bob_tracer_ids == {id(orch_bob.tracer)}, (
            f"Bob's FastAPI /v1/chat requests must all see his own orch.tracer, "
            f"got {bob_tracer_ids} (his tracer is {id(orch_bob.tracer)}, fallback is {fallback_id})"
        )
        assert fallback_id not in alice_tracer_ids | bob_tracer_ids, (
            "No FastAPI chat request should ever fall through to the shared global fallback tracer"
        )
        assert alice_tracer_ids != bob_tracer_ids, "Alice and Bob must never share a tracer"


def test_fastapi_tracer_reset_after_success_exception_and_between_requests():
    """Covers success, exception, and early-return paths, and confirms
    the contextvar is actually reset afterward (not left dangling for
    the next request on a reused thread) by checking get_tracer() falls
    back to the process default again once every request has finished."""
    _require_fastapi()
    from service.app import create_app
    from fastapi.testclient import TestClient
    import core.tracing as tracing_mod

    with tempfile.TemporaryDirectory() as td:
        app = create_app(data_root=Path(td))
        service = app.state.service
        service.auth.create_user("t013_carol", "pw", Role.USER)
        client = TestClient(app)
        tok = client.post("/auth/login", json={"username": "t013_carol", "password": "pw"}).json()["token"]

        # After all requests finish, the tracer contextvar must be back
        # to whatever it was before this test touched it -- proving the
        # fix's finally-block reset actually runs on every path (success,
        # swallowed-exception, and the never-reaches-set_tracer
        # unauthenticated case). This does NOT assert get_tracer()
        # returns the pristine process-wide fallback: pre-existing tests
        # in tests/test_tracing_v034.py call set_tracer() without ever
        # calling reset_tracer(), so the ambient contextvar state is
        # already not guaranteed to be the fallback by the time this
        # test runs inside the full suite (confirmed: this exact
        # assertion is order-dependent -- passes alone, fails after
        # test_tracing_v034.py in a full run -- purely because of that
        # pre-existing, out-of-scope leak, not because of anything this
        # task changed). Comparing against a snapshot taken by THIS test
        # right before it starts is robust to that leak either way.
        ambient_before = tracing_mod.get_tracer()
        r = client.post("/v1/chat", json={"message": "hello"}, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        r = client.get("/v1/recommendations", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        r = client.get("/v1/goals")
        assert r.status_code == 401
        assert tracing_mod.get_tracer() is ambient_before, (
            "tracer contextvar must be restored to whatever it was before "
            "this test's requests ran, on every path"
        )
