"""
PEAR 3.2 Task 021 -- workspace root authorization bypass.

Root cause (found by Task 020's closure audit): Workspace.add_root()
resolved whatever path it was given, mkdir'd it, and appended it to the
user's roots -- no concept of an authorized parent scope existed to
check against. The documented `workspace add <path>` chat command
(DesktopAgent._workspace_info) calls it straight from user text, so any
authenticated user could register another user's workspace (or any
system path) as their own sandbox root; every other operation
(list/copy/move/delete, all gated by require_inside(self.roots)) then
legitimately treated it as safe. Reachable through ordinary, unforced
chat routing (not just internal API calls).

Authorization model: Workspace now carries an explicit `boundary`,
established once at construction from the same source every other
per-user subsystem already uses (Orchestrator passes memory.persist_dir),
independent of the roots list. add_root() resolves first (so symlinks and
`..` cannot slip past), then requires the resolved path be the boundary
or beneath it, and refuses before any filesystem mutation. Checking
against the *current* roots was deliberately not used: self.roots starts
empty, so that would make the very first root un-addable. Constructing
Workspace(roots=...) without a boundary fails closed.

Sibling found by the targeted review: move_file(allow_outside=True)
skipped every boundary check, so an *approved* out-of-workspace move
would execute shutil.move() on any path, including another user's file.
It was unreachable only by accident (DesktopAgent._maybe_approval stores
src/dest as None for move approvals, so approving crashed). The
primitive now refuses anything beyond the boundary even when approved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.desktop as desktop
import core.tracing as tracing_mod
from core.desktop import Workspace
from core.memory import Memory
from core.llm import EchoLLM
from core.orchestrator import Orchestrator
from service.auth import Role


@pytest.fixture(autouse=True)
def _clean_tracer_context():
    """Service routes install a per-request tracer contextvar; on the
    main test thread that can outlive a test and bleed into later ones
    (same pre-existing fragility already documented for
    tests/test_tracing_v034.py). Force a clean slate, restore after."""
    original = tracing_mod._tracer_ctx.get()
    token = tracing_mod._tracer_ctx.set(None)
    try:
        yield
    finally:
        tracing_mod._tracer_ctx.reset(token)
        tracing_mod._tracer_ctx.set(original)


# ── Unit level: Workspace.add_root() ───────────────────────────────────

def _ws(tmp_path):
    mine = tmp_path / "alice"
    other = tmp_path / "bob"
    mine.mkdir()
    other.mkdir()
    return Workspace(roots=[mine], boundary=mine), mine, other


def _refused(fn) -> bool:
    try:
        fn()
    except PermissionError:
        return True
    return False


def test_add_root_rejects_other_users_directory(tmp_path):
    ws, mine, other = _ws(tmp_path)
    assert _refused(lambda: ws.add_root(other))
    assert ws.roots == [mine.resolve()]


def test_add_root_rejects_parent_traversal(tmp_path):
    ws, mine, other = _ws(tmp_path)
    assert _refused(lambda: ws.add_root(mine / ".." / "bob"))
    assert _refused(lambda: ws.add_root(mine / ".." / ".." / "etc"))


def test_add_root_rejects_absolute_system_paths(tmp_path):
    ws, _, _ = _ws(tmp_path)
    assert _refused(lambda: ws.add_root("/etc"))
    assert _refused(lambda: ws.add_root("/"))


def test_add_root_rejects_symlink_pointing_outside(tmp_path):
    ws, mine, other = _ws(tmp_path)
    link = mine / "escape_link"
    try:
        link.symlink_to(other)
    except OSError:
        pytest.skip("symlinks unsupported here")
    assert _refused(lambda: ws.add_root(link))
    assert _refused(lambda: ws.add_root(link / "nested"))
    assert all(r != other.resolve() for r in ws.roots)


def test_refusal_creates_nothing_on_disk(tmp_path):
    """Fail closed *before* mutating the filesystem: a rejected root
    must not leave a newly created directory behind."""
    ws, mine, _ = _ws(tmp_path)
    target = tmp_path / "ghost_outside" / "deep"
    assert _refused(lambda: ws.add_root(target))
    assert not target.exists()
    assert not target.parent.exists()


def test_add_root_accepts_legitimate_nested_roots(tmp_path):
    ws, mine, _ = _ws(tmp_path)
    existing = mine / "projects"
    existing.mkdir()
    assert ws.add_root(existing) == existing.resolve()
    created = ws.add_root(mine / "new" / "deeper")
    assert created.is_dir()
    assert ws.add_root(mine) == mine.resolve()  # the boundary itself is fine
    assert {existing.resolve(), created} <= set(ws.roots)


def test_constructor_requires_explicit_boundary_for_explicit_roots(tmp_path):
    """No safe default exists for a service-scoped workspace -> fail closed."""
    with pytest.raises(PermissionError):
        Workspace(roots=[tmp_path / "alice"])


def test_constructor_rejects_initial_root_outside_boundary(tmp_path):
    mine = tmp_path / "alice"
    other = tmp_path / "bob"
    mine.mkdir()
    other.mkdir()
    with pytest.raises(PermissionError):
        Workspace(roots=[other], boundary=mine)


def test_bare_workspace_boundary_is_home_not_unrestricted(tmp_path, monkeypatch):
    """CLI / ad-hoc construction keeps working inside the user's home
    but is no longer genuinely unrestricted."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ws = Workspace()
    assert ws.boundary == home.resolve()
    assert _refused(lambda: ws.add_root(tmp_path / "elsewhere"))
    assert ws.add_root(home / "docs") == (home / "docs").resolve()


def test_boundary_helpers_fail_closed_without_boundary(tmp_path):
    ws, mine, _ = _ws(tmp_path)
    assert ws.in_boundary(mine / "x") is True
    del ws.boundary
    assert ws.in_boundary(mine / "x") is False
    with pytest.raises(PermissionError):
        ws.require_in_boundary(mine / "x")


# ── Unit level: the sibling (approved out-of-workspace move/copy) ─────

def test_move_allow_outside_cannot_cross_the_boundary(tmp_path):
    ws, mine, other = _ws(tmp_path)
    victim = other / "private.txt"
    victim.write_text("BOB")
    with pytest.raises(PermissionError):
        desktop.move_file(victim, mine / "taken.txt", workspace=ws, allow_outside=True)
    assert victim.exists() and not (mine / "taken.txt").exists()
    # and the reverse direction (writing into the other user's tree)
    mine_file = mine / "m.txt"
    mine_file.write_text("A")
    with pytest.raises(PermissionError):
        desktop.move_file(mine_file, other / "planted.txt", workspace=ws, allow_outside=True)
    assert mine_file.exists() and not (other / "planted.txt").exists()


def test_move_allow_outside_still_works_inside_the_boundary(tmp_path):
    """Approval keeps its legitimate meaning: touching something outside
    the workspace *roots* but within the user's own authorized boundary."""
    ws, mine, _ = _ws(tmp_path)
    inside_root = mine / "workspace_root"
    inside_root.mkdir()
    ws2 = Workspace(roots=[inside_root], boundary=mine)
    src = inside_root / "a.txt"
    src.write_text("hi")
    sibling_dir = mine / "other_area"
    sibling_dir.mkdir()
    r = desktop.move_file(src, sibling_dir / "a.txt", workspace=ws2, allow_outside=True)
    assert r["ok"] is True
    assert (sibling_dir / "a.txt").read_text() == "hi"


def test_copy_allow_outside_cannot_cross_the_boundary(tmp_path):
    ws, mine, other = _ws(tmp_path)
    victim = other / "private.txt"
    victim.write_text("BOB")
    with pytest.raises(PermissionError):
        desktop.copy_file(victim, mine / "got.txt", workspace=ws, allow_outside=True)
    assert not (mine / "got.txt").exists()


# ── Orchestrator wiring ────────────────────────────────────────────────

def test_orchestrator_workspace_boundary_is_the_users_persist_dir(tmp_path):
    a = Orchestrator(memory=Memory(session_id="a", persist_dir=tmp_path / "alice"), llm=EchoLLM())
    b = Orchestrator(memory=Memory(session_id="b", persist_dir=tmp_path / "bob"), llm=EchoLLM())
    assert a.workspace.boundary == (tmp_path / "alice").resolve()
    assert b.workspace.boundary == (tmp_path / "bob").resolve()
    assert a.workspace.boundary != b.workspace.boundary
    assert _refused(lambda: a.workspace.add_root(b.workspace.roots[0]))


# ── End to end over the real HTTP surfaces ────────────────────────────

class _Surface:
    """Real request path: login -> /v1/chat -> sessions -> orchestrator."""

    def __init__(self, kind: str, root: Path):
        self.kind = kind
        if kind == "stdlib":
            from service.app import PearService
            self.svc = PearService(data_root=root / "svc")
            self.service = self.svc
        else:
            try:
                import fastapi  # noqa: F401
                import uvicorn  # noqa: F401
            except Exception:
                pytest.skip("fastapi/uvicorn not installed")
            from fastapi.testclient import TestClient
            from service.app import create_app
            app = create_app(data_root=root / "svc")
            self.service = app.state.service
            self.client = TestClient(app)
        self.tokens = {}
        for u in ("alice", "bob"):
            self.service.auth.create_user(u, "pw", Role.USER)
            if kind == "stdlib":
                _, body = self.svc.do_login(u, "pw")
                self.tokens[u] = body["token"]
            else:
                self.tokens[u] = self.client.post(
                    "/auth/login", json={"username": u, "password": "pw"}
                ).json()["token"]

    def orch(self, user):
        return self.service.sessions.get(user).orchestrator

    def chat(self, user, message):
        if self.kind == "stdlib":
            status, body = self.svc.handle_route(
                "POST", "/v1/chat",
                {"Authorization": f"Bearer {self.tokens[user]}"},
                json.dumps({"message": message}).encode(),
            )
            assert status == 200
            return body["result"]
        r = self.client.post(
            "/v1/chat", json={"message": message},
            headers={"Authorization": f"Bearer {self.tokens[user]}"},
        )
        assert r.status_code == 200
        return r.json()["result"]


@pytest.fixture(params=["stdlib", "fastapi"])
def surface(request, tmp_path):
    return _Surface(request.param, tmp_path)


def _setup_victim(s: _Surface):
    oa, ob = s.orch("alice"), s.orch("bob")
    aw, bw = Path(oa.workspace.roots[0]), Path(ob.workspace.roots[0])
    secret = bw / "bob_private_notes.txt"
    secret.write_text("BOB_CONFIDENTIAL_12345")
    return oa, ob, aw, bw, secret


def test_http_original_exploit_phrase_no_longer_expands_the_sandbox(surface):
    """The exact natural-routing exploit from Task 020 (previously added
    the root on this phrase, reached via real unforced routing)."""
    oa, ob, aw, bw, secret = _setup_victim(surface)
    before = list(oa.workspace.roots)
    surface.chat("alice", f"workspace add {bw} desktop files applications")
    assert oa.workspace.roots == before
    assert not any(str(bw) in str(r) for r in oa.workspace.roots)
    # nothing created either (the old code would mkdir whatever it was given)
    assert not Path(f"{bw} desktop files applications").exists()


def test_http_cross_user_move_and_its_approval_cannot_succeed(surface):
    oa, ob, aw, bw, secret = _setup_victim(surface)
    r = surface.chat("alice", f"desktop files move {secret} to {aw}/taken.txt")
    assert r["agent"] == "desktop", "must actually reach the desktop agent (not vacuous)"
    assert secret.exists() and not (aw / "taken.txt").exists()

    # White-box: populate the approval record the way a fix of the
    # None-field bug would, then approve over the real chat path. The
    # exact phrase matters: DesktopAgent matches r"approve\s+desktop\s+(\w+)"
    # from the START of the message, and over real routing that phrase
    # reaches the handler via the planner. (An earlier draft prefixed
    # "desktop " and only ever hit the help text -- a vacuous assertion
    # that passed even against the unfixed code.)
    agent = oa.agents["desktop"]
    agent.pending_approvals["mov_9999"] = {
        "action": "move_file", "src": str(secret), "dest": str(aw / "taken2.txt"), "message": "m",
    }
    r2 = surface.chat("alice", "approve desktop mov_9999")
    assert "mov_9999" not in agent.pending_approvals, "approval handler must actually have fired"
    text = str(r2.get("reply") or r2.get("error") or "").lower()
    assert "boundary" in text, f"must be refused for the boundary reason, got: {text[:200]}"
    assert secret.exists(), "Bob's file must not move"
    assert not (aw / "taken2.txt").exists()
    assert secret.read_text() == "BOB_CONFIDENTIAL_12345"


def test_http_cross_user_delete_and_its_approval_cannot_succeed(surface):
    oa, ob, aw, bw, secret = _setup_victim(surface)
    r = surface.chat("alice", f"desktop files delete {secret}")
    assert r["agent"] == "desktop"
    import re
    m = re.search(r"approve desktop (\w+)", str(r.get("reply")))
    assert m, f"delete of an out-of-workspace path should prompt for approval, got: {r.get('reply')!r}"
    approval_id = m.group(1)
    r2 = surface.chat("alice", f"approve desktop {approval_id}")
    assert approval_id not in oa.agents["desktop"].pending_approvals, "handler must have fired"
    assert "outside" in str(r2.get("reply") or r2.get("error") or "").lower()
    assert secret.exists()


def test_http_cross_user_copy_cannot_exfiltrate(surface):
    """Outcome guard only. Over plain chat a fresh session routes a bare
    `copy ...` to the echo-mode personal agent, so this does not by itself
    prove the desktop handler refused -- the reason-level assertion for
    copy lives in test_forced_desktop_route_original_exploit_sequence_is_blocked,
    where the desktop handler is reached deterministically."""
    oa, ob, aw, bw, secret = _setup_victim(surface)
    surface.chat("alice", f"copy {secret} to {aw}/x.txt")
    assert not (aw / "x.txt").exists()
    assert secret.read_text() == "BOB_CONFIDENTIAL_12345"


def test_http_legitimate_operations_still_work(surface):
    oa, ob, aw, bw, secret = _setup_victim(surface)
    # Alice's own workspace works normally
    (aw / "mine.txt").write_text("hello")
    r = surface.chat("alice", f"desktop files copy {aw}/mine.txt to {aw}/mine2.txt")
    assert r["agent"] == "desktop" and r["ok"] is True
    assert (aw / "mine2.txt").read_text() == "hello"
    # Bob's own workspace works normally for Bob
    (bw / "b.txt").write_text("bob data")
    r = surface.chat("bob", f"desktop files copy {bw}/b.txt to {bw}/b2.txt")
    assert r["agent"] == "desktop" and r["ok"] is True
    assert (bw / "b2.txt").read_text() == "bob data"
    # and Alice's legitimate nested root registration (route-level, forced
    # agent: the HTTP chat path can't force an agent, and a natural phrase
    # would swallow trailing words into the path)
    r = oa.route(f"workspace add {aw}/projects", preferred="desktop")
    assert r["ok"] is True
    assert (aw / "projects").resolve() in oa.workspace.roots


def test_forced_desktop_route_original_exploit_sequence_is_blocked(tmp_path):
    """Task 020's forced-agent reproduction, end to end at the
    orchestrator level: register, list, read-via-copy, delete."""
    from service.app import PearService
    svc = PearService(data_root=tmp_path / "svc")
    for u in ("alice", "bob"):
        svc.auth.create_user(u, "pw", Role.USER)
    oa, ob = svc.sessions.get("alice").orchestrator, svc.sessions.get("bob").orchestrator
    aw, bw = Path(oa.workspace.roots[0]), Path(ob.workspace.roots[0])
    secret = bw / "s.txt"
    secret.write_text("BOB")

    r = oa.route(f"workspace add {bw}", preferred="desktop")
    assert r["ok"] is False and "boundary" in (r.get("error") or "")
    assert oa.workspace.roots == [aw.resolve()]
    assert oa.route(f"list dir {bw}", preferred="desktop")["ok"] is False
    rc = oa.route(f"copy {secret} to {aw}/stolen.txt", preferred="desktop")
    assert rc["ok"] is False and "outside" in str(rc.get("error") or rc.get("reply") or "").lower()
    assert not (aw / "stolen.txt").exists()
    rd = oa.route(f"delete {secret}", preferred="desktop")
    assert rd.get("action") == "needs_approval"
    ra = oa.route(f"approve desktop {rd['approval_id']}", preferred="desktop")
    assert "outside" in str(ra.get("error") or ra.get("reply") or "").lower()
    assert secret.exists()
    # and Bob is unaffected
    assert ob.route(f"list dir {bw}", preferred="desktop")["ok"] is True
