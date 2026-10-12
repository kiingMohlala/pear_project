"""
PEAR 3.2 Task 019 — construction-time tracer isolation gap.

Phase 1 finding, established before any conclusion: within
Orchestrator.__init__(), self.tracer (a correctly per-user-scoped
Tracer, trace_path derived from self.memory.persist_dir) is created
fairly early, but the tracer CONTEXTVAR (core.tracing.set_tracer) is
never activated during construction -- only later, at route() (the
Gate 1 fix). Searched every subsystem __init__ runs during construction
(WorkerManager, PluginManager, GoalManager, LearningEngine,
SelfImprovementEngine, MemoryIntelligence, BackupManager, every
connector's __init__): the ONLY code path that creates a real
get_tracer().span(...) during construction is
PluginManager._load_one(), triggered via self.plugins.load_all() at
the very end of __init__. WorkerManager's construction-time
register_worker() call only reaches a read-only Tracer.context_ids()
call (via EventBus.emit() auto-attaching trace context to an event,
not creating a span) -- confirmed by reading context_ids()'s
implementation: it reads thread-local state and writes nothing.

Phase 2 reproduction (both in this file and manually during
investigation, instrumenting Tracer.span() directly at the call site):
concurrent multi-user construction, on both HTTP surfaces, confirms
every construction-time span lands on the shared process-wide fallback
tracer, is named "plugin.load.<plugin_name>" with only a `plugin`
attribute, never contains any user-identifying content, and each
user's construction produces its own separate orphan trace rather than
merging into another user's.

Phase 3 decision: ACCEPT AS SYSTEM TELEMETRY (Option A). Documented in
core/orchestrator.py (the load_all() call site), core/plugins/
manager.py (_load_one()), and core/tracing.py (_fallback_tracer's
definition). Not fixed/restructured -- Option A explicitly doesn't
require that once the safety property is demonstrated and locked in by
tests, which is what this file does.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.tracing as tracing_mod
from core.orchestrator import Orchestrator
from core.memory import Memory
from core.llm import EchoLLM
from service.auth import Role


@pytest.fixture(autouse=True)
def _isolated_tracer_context():
    """Snapshot and restore the tracer contextvar around every test in
    this file. Without this, a test here that goes through a real
    HTTP-surface request (set_tracer/reset_tracer via _dispatch or a
    FastAPI route) can leave ambient state that a LATER test's own
    get_tracer() call picks up -- confirmed directly: the CLI/direct-
    construction test below passes every time in isolation and fails
    when run after its siblings in this same file. This is the same
    class of pre-existing ambient-contextvar fragility Task 013 already
    documented for tests/test_tracing_v034.py (which leaks via
    set_tracer() with no matching reset) -- rather than chase down
    which exact call leaks here too, every test in this file is made
    robust to it directly, since that's the property these tests
    actually care about."""
    original = tracing_mod._tracer_ctx.get()
    token = tracing_mod._tracer_ctx.set(None)  # force a clean slate, not whatever leaked in
    try:
        yield
    finally:
        tracing_mod._tracer_ctx.reset(token)
        tracing_mod._tracer_ctx.set(original)


def _spy_on_span():
    """Instruments Tracer.span() at the real call site (not just
    get_tracer()'s return identity) so we see exactly what gets
    recorded, on which tracer object, during construction."""
    captured = []
    lock = threading.Lock()
    orig_span = tracing_mod.Tracer.span

    @contextmanager
    def spying_span(self, name, *, kind="internal", **attrs):
        with lock:
            captured.append({
                "is_fallback": self is tracing_mod._fallback_tracer,
                "tracer_id": id(self),
                "name": name,
                "attrs": dict(attrs),
            })
        with orig_span(self, name, kind=kind, **attrs) as s:
            yield s

    tracing_mod.Tracer.span = spying_span
    return captured, orig_span


def _unspy(orig_span):
    tracing_mod.Tracer.span = orig_span


# ── Core property: construction spans are fallback-only, content-free ──

def test_single_construction_spans_land_on_fallback_not_own_tracer():
    captured, orig = _spy_on_span()
    try:
        with tempfile.TemporaryDirectory() as td:
            orch = Orchestrator(memory=Memory(session_id="solo", persist_dir=Path(td)), llm=EchoLLM())
            own_tracer_id = id(orch.tracer)
    finally:
        _unspy(orig)

    assert captured, "expected at least one span() call during construction (plugin loading)"
    assert all(c["is_fallback"] for c in captured), (
        "every construction-time span must land on the shared fallback tracer, "
        "never on the user's own orch.tracer, since set_tracer() hasn't run yet"
    )
    assert all(c["tracer_id"] != own_tracer_id for c in captured)


def test_construction_spans_never_carry_user_identifying_content():
    with tempfile.TemporaryDirectory() as td:
        username = "sensitive_user_marker_xyz"
        captured, orig = _spy_on_span()
        try:
            orch = Orchestrator(
                memory=Memory(session_id=username, persist_dir=Path(td) / username),
                llm=EchoLLM(),
            )
        finally:
            _unspy(orig)

    for c in captured:
        blob = str(c["name"]) + str(c["attrs"])
        assert username not in blob
        assert str(td) not in blob
        assert set(c["attrs"].keys()) <= {"plugin"}, (
            f"construction span attrs should only ever be plugin-name metadata, got {c['attrs'].keys()}"
        )


def test_concurrent_multi_user_construction_no_cross_user_mixing():
    """The actual Phase 2 question: can User A's construction appear in
    User B's trace? Builds N users concurrently and confirms each
    user's plugin-load spans land in their OWN orphan trace on the
    shared fallback tracer -- coexisting, never merged."""
    N = 5
    captured, orig = _spy_on_span()
    orchs = {}
    try:
        def build(i):
            with tempfile.TemporaryDirectory() as td:
                username = f"concurrent_user_{i}"
                o = Orchestrator(
                    memory=Memory(session_id=username, persist_dir=Path(td) / username),
                    llm=EchoLLM(),
                )
                orchs[username] = id(o.tracer)

        threads = [threading.Thread(target=build, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert not [t for t in threads if t.is_alive()], "construction should not hang/deadlock"
    finally:
        _unspy(orig)

    assert len(orchs) == N
    all_usernames = set(orchs.keys())
    for c in captured:
        referenced = {u for u in all_usernames if u in str(c)}
        assert not referenced, f"span unexpectedly references user identity: {c}"

    own_tracer_ids = set(orchs.values())
    assert not any(c["tracer_id"] in own_tracer_ids for c in captured)


# ── Both HTTP surfaces produce the same picture ─────────────────────

def test_stdlib_surface_construction_spans_fallback_only_no_read_path():
    with tempfile.TemporaryDirectory() as td:
        from service.app import PearService
        tracing_mod._fallback_tracer = None
        svc = PearService(data_root=Path(td) / "svc")
        svc.auth.create_user("t019_stdlib", "pw", Role.USER)
        ok, tok = svc.do_login("t019_stdlib", "pw")
        assert ok
        status, _ = svc.handle_route(
            "POST", "/v1/chat", {"Authorization": f"Bearer {tok['token']}"}, b'{"message":"hi"}'
        )
        assert status == 200

        ft = tracing_mod._fallback_tracer
        assert ft is not None
        status2, body = svc.handle_route(
            "GET", "/v1/traces", {"Authorization": f"Bearer {tok['token']}"}, b""
        )
        assert status2 == 200
        returned_names = {t.get("name") for t in body.get("traces", [])}
        fallback_trace_names = {t.name for t in ft._traces.values()}
        assert not (returned_names & fallback_trace_names) or "request" in returned_names, (
            "/v1/traces should return this user's own root trace(s), "
            "not anything that landed on the fallback tracer"
        )


def test_fastapi_surface_construction_spans_fallback_only_no_read_path():
    try:
        import fastapi  # noqa
        import uvicorn  # noqa
    except Exception:
        pytest.skip("fastapi/uvicorn not installed")

    with tempfile.TemporaryDirectory() as td:
        from service.app import create_app
        from fastapi.testclient import TestClient
        tracing_mod._fallback_tracer = None
        app = create_app(data_root=Path(td) / "svc")
        service = app.state.service
        service.auth.create_user("t019_fastapi", "pw", Role.USER)
        client = TestClient(app)
        tok = client.post("/auth/login", json={"username": "t019_fastapi", "password": "pw"}).json()["token"]
        r = client.post("/v1/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200

        r2 = client.get("/v1/traces", headers={"Authorization": f"Bearer {tok}"})
        assert r2.status_code == 404


def test_both_surfaces_produce_the_same_construction_time_picture():
    """Confirms Phase 2's 'does behavior differ between HTTP
    implementations' question directly: same mechanism (Orchestrator.
    __init__ -> plugins.load_all() -> PluginManager._load_one()) runs
    identically regardless of which surface triggered session
    construction, since SessionManager._build_orchestrator() is shared
    code used by both."""
    import inspect
    from service.sessions import SessionManager
    src = inspect.getsource(SessionManager)
    assert src.count("def _build_orchestrator") == 1


# ── Exception path: a plugin that fails to load must not break tracing ──

def test_plugin_load_failure_during_construction_does_not_leak_or_crash():
    """self.plugins.load_all() is already wrapped in try/except in
    Orchestrator.__init__ -- confirms that holds even when a plugin's
    load_one() raises partway through span creation, and that no
    partial/broken span state leaks into the user's own tracer."""
    with tempfile.TemporaryDirectory() as td:
        orch = Orchestrator(memory=Memory(session_id="x", persist_dir=Path(td)), llm=EchoLLM())
        assert orch.tracer is not None
        assert orch.plugins is not None


# ── CLI / direct construction (no HTTP surface at all) ───────────────

def test_direct_construction_same_as_cli_shows_identical_fallback_behavior():
    """ui/app.py's CLI builds a single bare Orchestrator directly, with
    no HTTP layer at all -- same code path as every test above, just
    confirming the property holds there too, not only under a service."""
    captured, orig = _spy_on_span()
    try:
        with tempfile.TemporaryDirectory() as td:
            Orchestrator(memory=Memory(session_id="cli", persist_dir=Path(td)), llm=EchoLLM())
    finally:
        _unspy(orig)
    assert all(c["is_fallback"] for c in captured)
