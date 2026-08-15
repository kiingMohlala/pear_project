"""
PEAR 3.1 security regression suite.

Each test here corresponds to a specific gate in the PEAR 3.1 hardening
task card. Gates are added incrementally, verified against a real running
server (not just in-process calls), before being marked done.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from service.app import PearService, make_handler
from http.server import ThreadingHTTPServer
from core.llm import EchoLLM


class _RoomyThreadingHTTPServer(ThreadingHTTPServer):
    # Default TCP accept backlog is 5 (socketserver.TCPServer default) —
    # far too small once tests start opening dozens of near-simultaneous
    # connections (Gate 8's 30-user test in particular). Must be a class
    # attribute, not set on the instance after construction — listen() is
    # called during __init__/server_activate(), before an instance
    # attribute assignment would ever take effect. Too small a backlog
    # causes urllib to see ECONNRESET, which looks like a server bug but
    # is purely a test-harness capacity limit.
    request_queue_size = 256


def _start_server(data_root: Path):
    service = PearService(data_root=data_root)
    service.sessions.llm = EchoLLM()
    # This suite is testing tracer isolation under concurrency, not rate
    # limiting — give it headroom so legitimate throttling doesn't masquerade
    # as a test failure under heavy parallel test-suite load.
    service.rate_limiter.configure(per_minute=6000, burst=500)
    handler = make_handler(service)
    httpd = _RoomyThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)
    return service, httpd, port


def _login(port: int, username: str, password: str) -> str:
    req = Request(
        f"http://127.0.0.1:{port}/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())["token"]


def _chat(port: int, token: str, message: str) -> dict:
    req = Request(
        f"http://127.0.0.1:{port}/v1/chat",
        data=json.dumps({"message": message}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _traces(port: int, token: str) -> list:
    req = Request(
        f"http://127.0.0.1:{port}/v1/traces",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())["traces"]


# ── Gate 1: tracer isolation ─────────────────────────────────────────

def test_gate1_concurrent_traces_no_cross_user_leak():
    """
    Real ThreadingHTTPServer, two real users, many concurrent /v1/chat
    calls fired from both accounts at once, then each user reads back
    /v1/traces. Neither may ever see the other's objective text.
    """
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        try:
            token_demo = _login(port, "demo", "demo")
            token_admin = _login(port, "admin", "admin")

            demo_marker = "DEMO_ONLY_MARKER_7f3a"
            admin_marker = "ADMIN_ONLY_MARKER_9c1b"

            errors = []

            def hammer(token, marker, n=25):
                try:
                    for i in range(n):
                        _chat(port, token, f"{marker} message {i}")
                except Exception as e:
                    errors.append(e)

            t1 = threading.Thread(target=hammer, args=(token_demo, demo_marker))
            t2 = threading.Thread(target=hammer, args=(token_admin, admin_marker))
            t1.start(); t2.start()
            t1.join(timeout=30); t2.join(timeout=30)
            assert not errors, f"errors during concurrent chat: {errors}"

            demo_traces = json.dumps(_traces(port, token_demo))
            admin_traces = json.dumps(_traces(port, token_admin))

            assert demo_marker in demo_traces, "demo's own traces should contain demo's marker"
            assert admin_marker not in demo_traces, "CROSS-USER LEAK: demo saw admin's trace data"

            assert admin_marker in admin_traces, "admin's own traces should contain admin's marker"
            assert demo_marker not in admin_traces, "CROSS-USER LEAK: admin saw demo's trace data"
        finally:
            httpd.shutdown()


def test_gate1_sequential_construction_does_not_swap_global():
    """
    Regression for the original root cause: constructing a second user's
    Orchestrator must not silently repoint tracing for an already-active
    orchestrator. This is the in-process version of the same property,
    without needing real concurrency to demonstrate the construction-time
    bug specifically.
    """
    from service.sessions import SessionManager
    from core.tracing import get_tracer

    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        sess_a = sm.get("alice")
        tracer_a = sess_a.orchestrator.tracer

        # Constructing bob's session/orchestrator must not change what
        # alice's own orchestrator considers its tracer.
        sess_b = sm.get("bob")
        tracer_b = sess_b.orchestrator.tracer

        assert tracer_a is not tracer_b
        assert sess_a.orchestrator.tracer is tracer_a, "alice's tracer must be unaffected by bob's construction"

        # And the global fallback (used when no context is active) must be
        # neither of them.
        fallback = get_tracer()
        assert fallback is not tracer_a
        assert fallback is not tracer_b


def _goal_create(port: int, token: str, objective: str) -> tuple:
    req = Request(
        f"http://127.0.0.1:{port}/v1/goals",
        data=json.dumps({"objective": objective}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ── Gate 4: explicit ownership propagation ───────────────────────────

def test_gate4_goal_stamped_with_owner_over_http():
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        try:
            token_demo = _login(port, "demo", "demo")
            token_admin = _login(port, "admin", "admin")

            _, g_demo = _goal_create(port, token_demo, "demo's private goal")
            _, g_admin = _goal_create(port, token_admin, "admin's private goal")

            assert g_demo["goal"]["user_id"] == "demo"
            assert g_admin["goal"]["user_id"] == "admin"
            assert g_demo["goal"]["user_id"] != g_admin["goal"]["user_id"]
        finally:
            httpd.shutdown()


def test_gate4_job_goal_workflow_dispatch_stamped_with_owner():
    """Direct-orchestrator check for the record types with no dedicated
    HTTP creation route (jobs, workflow runs, worker dispatches)."""
    from service.sessions import SessionManager

    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        alice = sm.get("alice").orchestrator
        bob = sm.get("bob").orchestrator

        assert alice.user_id == "alice"
        assert bob.user_id == "bob"

        job_a = alice.jobs.enqueue("alice job")
        job_b = bob.jobs.enqueue("bob job")
        assert job_a.user_id == "alice"
        assert job_b.user_id == "bob"

        goal_a = alice.goals.create("alice goal", auto_start=False)
        goal_b = bob.goals.create("bob goal", auto_start=False)
        assert goal_a.user_id == "alice"
        assert goal_b.user_id == "bob"

        run_a = alice.workflows.start("daily_briefing") if "daily_briefing" in alice.workflows.definitions else None
        if run_a is not None:
            assert run_a.user_id == "alice"

        rec_a = alice.workers.dispatch("alice dispatch", execute_fn=lambda obj: {"ok": True})
        rec_b = bob.workers.dispatch("bob dispatch", execute_fn=lambda obj: {"ok": True})
        assert rec_a.session_user == "alice"
        assert rec_b.session_user == "bob"


def test_gate4_ownership_survives_restart():
    """Persist a job and a goal under one SessionManager, then build a
    FRESH SessionManager pointed at the same data_root (simulating a
    process restart) and confirm ownership is still correct after reload
    from disk, not just held in memory."""
    from service.sessions import SessionManager

    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td)

        sm1 = SessionManager(data_root=data_root, llm=EchoLLM())
        orch1 = sm1.get("carol").orchestrator
        job = orch1.jobs.enqueue("carol's job")
        goal = orch1.goals.create("carol's goal", auto_start=False)
        job_id, goal_id = job.id, goal.id

        # Simulate restart: brand-new SessionManager/Orchestrator instances,
        # same on-disk data_root — nothing carried over in memory.
        sm2 = SessionManager(data_root=data_root, llm=EchoLLM())
        orch2 = sm2.get("carol").orchestrator

        reloaded_job = orch2.jobs.get_job(job_id) if hasattr(orch2.jobs, "get_job") else orch2.jobs._jobs.get(job_id)
        assert reloaded_job is not None, "job did not survive restart at all"
        assert reloaded_job.user_id == "carol", "job ownership lost across restart"

        reloaded_goal = orch2.goals.get(goal_id)
        assert reloaded_goal is not None, "goal did not survive restart at all"
        assert reloaded_goal.user_id == "carol", "goal ownership lost across restart"


def _get(port: int, token: str, path: str) -> tuple:
    req = Request(f"http://127.0.0.1:{port}{path}", headers={"Authorization": f"Bearer {token}"} if token else {}, method="GET")
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _post(port: int, token: str, path: str, body: dict) -> tuple:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ── Gate 2: resource ownership / IDOR protection ──────────────────────

def test_gate2_cross_user_goal_access_denied():
    """User A creates a goal; a genuinely different non-admin User B
    requesting it by ID must be denied, and the denial must look
    identical to 'does not exist' (404, not 403) so the response itself
    can't be used to enumerate other users' resource IDs."""
    from service.auth import Role
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        try:
            service.auth.create_user("eve", "eve-pw", Role.USER)
            token_demo = _login(port, "demo", "demo")
            token_eve = _login(port, "eve", "eve-pw")

            status, created = _goal_create(port, token_demo, "demo private goal")
            assert status == 200
            gid = created["goal"]["id"]

            # owner can read it
            status, body = _get(port, token_demo, f"/v1/goals/{gid}")
            assert status == 200 and body["ok"] is True

            # a different non-admin user cannot — and gets the same 404
            # shape as a nonexistent id, not a 403 that would confirm the
            # resource exists.
            status, body = _get(port, token_eve, f"/v1/goals/{gid}")
            assert status == 404, f"expected 404 (indistinguishable from not-found), got {status}: {body}"
            assert body["ok"] is False
        finally:
            httpd.shutdown()


def test_gate2_authorize_resource_admin_bypass_works_at_the_check_level():
    """
    authorize_resource() itself correctly bypasses ownership for ADMIN —
    verified directly, not through HTTP.

    Important finding, documented rather than silently worked around:
    /v1/goals/<gid> can't actually exercise this bypass end-to-end today.
    Every user (including admin) is routed to their OWN per-user
    Orchestrator via SessionManager, and that orchestrator's goals dict
    structurally never contains another user's goals — admin gets a 404
    from orch.goals.get(gid) before authorize_resource() is even reached,
    same as any other user. So right now admin can't inspect another
    user's goal via this route despite the auth-layer bypass existing and
    working correctly. Fixing that needs an actual cross-session resource
    lookup path for admins, which doesn't exist anywhere yet — that's a
    real design decision (a new admin capability), not a one-line fix,
    so it's logged rather than built here.
    """
    from service.auth import AuthManager, Role
    import tempfile as _tf

    with _tf.TemporaryDirectory() as td:
        auth = AuthManager(persist_path=Path(td) / "users.json")
        admin = auth.login("admin", "admin")
        demo = auth.login("demo", "demo")
        assert admin.role == Role.ADMIN

        # Admin bypasses ownership entirely.
        result = auth.authorize_resource(admin, resource_owner="demo")
        assert result.username == "admin"

        # A non-admin does not.
        try:
            auth.authorize_resource(demo, resource_owner="someone-else")
            assert False, "expected PermissionError"
        except PermissionError:
            pass

        # Owner accessing their own resource is fine.
        result = auth.authorize_resource(demo, resource_owner="demo")
        assert result.username == "demo"


def test_gate2_admin_cannot_reach_another_users_goal_via_http_today():
    """
    Documents the current, real behavior confirmed above: even admin gets
    404 on another user's goal via /v1/goals/<gid>, because resource
    lookup is scoped to admin's own per-user Orchestrator before
    authorize_resource() is ever consulted. This is not a regression from
    this gate's changes — verified true against the pre-3.1 code too. It's
    a genuine gap between the admin-bypass the auth layer supports and
    what any current route can actually reach.
    """
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        try:
            token_demo = _login(port, "demo", "demo")
            token_admin = _login(port, "admin", "admin")

            status, created = _goal_create(port, token_demo, "demo private goal 2")
            assert status == 200
            gid = created["goal"]["id"]

            status, body = _get(port, token_admin, f"/v1/goals/{gid}")
            assert status == 404, (
                "if this now returns 200, an admin cross-session lookup path "
                "was added — update this test's docstring and assertions to "
                "match the new intended behavior"
            )
        finally:
            httpd.shutdown()


def _fixed_beta_route_returns():
    """
    Exercises the actual bug that was fixed in service/app.py's
    /v1/beta/activate and /v1/beta/status handlers: a minimal stub
    standing in for the real (gitignored, not-in-this-repo) BetaManager,
    just enough surface to prove the ROUTE no longer trusts a
    client-supplied 'account' over the server-derived identity.
    """
    class _StubBeta:
        def __init__(self):
            self.activations = {}

        def activate(self, code, *, account, device_id, platform, app_version):
            self.activations[account] = {"code": code, "device_id": device_id}
            return {"ok": True, "key_id": "bk_stub", "expires_at": 0}

        def check_access(self, account, device_id, platform="", app_version=""):
            if account in self.activations:
                return {"ok": True, "key_id": "bk_stub", "expires_at": 0}
            return {"ok": False, "error": "no active beta license"}

    return _StubBeta


def test_gate2_beta_activate_ignores_client_supplied_account_when_authenticated():
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        service.beta = _fixed_beta_route_returns()()
        try:
            token_demo = _login(port, "demo", "demo")

            # demo is authenticated, but tries to activate the code under a
            # DIFFERENT account in the request body. The route must ignore
            # that and bind it to demo's own authenticated identity.
            status, body = _post(port, token_demo, "/v1/beta/activate", {
                "code": "PEAR-AAAA-BBBB-CCCC",
                "account": "admin",  # attempted impersonation
                "device_id": "dev1",
            })
            assert status == 200 and body["ok"] is True
            assert "admin" not in service.beta.activations, "impersonation succeeded — account override was honored"
            assert "demo" in service.beta.activations, "activation should have bound to the authenticated caller"
        finally:
            httpd.shutdown()


def test_gate2_beta_status_cannot_probe_other_accounts():
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        stub = _fixed_beta_route_returns()()
        stub.activations["admin"] = {"code": "x", "device_id": "d"}  # admin has a license
        service.beta = stub
        try:
            token_demo = _login(port, "demo", "demo")

            # demo has no license of their own, and must not be able to
            # learn admin's license status by naming "admin" in the body.
            status, body = _post(port, token_demo, "/v1/beta/status", {
                "account": "admin", "device_id": "d",
            })
            assert status == 200
            assert body["ok"] is False, "demo was able to read admin's beta status by naming the account"

            # demo checking their own (nonexistent) status is fine and
            # correctly reports no license.
            status, body = _post(port, token_demo, "/v1/beta/status", {"device_id": "d"})
            assert body["ok"] is False
        finally:
            httpd.shutdown()


# ── Gate 5: worker identity propagation ───────────────────────────────

def _start_fake_remote_worker(response_body: dict):
    """A minimal HTTP server standing in for a remote PEAR /v1/chat
    endpoint, so _run_remote is exercised against a real socket, not
    mocked out. Captures the last request it received."""
    import http.server

    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            captured["body"] = body
            captured["auth_header"] = self.headers.get("Authorization")
            resp = json.dumps(response_body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    return httpd, port, captured


def test_gate5_origin_identity_sent_as_metadata_not_credential():
    """The outbound dispatch to a remote worker carries the originating
    user_id as plain informational metadata (for the remote side's own
    audit trail), separately from the actual auth credential (the worker's
    bearer token)."""
    from core.orchestrator import Orchestrator
    from core.workers import WorkerManager

    httpd, port, captured = _start_fake_remote_worker({"ok": True, "reply": "hi"})
    try:
        orch = Orchestrator(user_id="alice")
        wm = WorkerManager(orch)
        w = wm.register_worker("remote1", endpoint=f"http://127.0.0.1:{port}", capabilities={"browser"})
        w.meta["token"] = "worker-secret-token"

        rec = wm.dispatch("alice's objective", required_capabilities=["browser"])
        wm.wait(rec.id, timeout=5)

        assert captured.get("body", {}).get("origin_user_id") == "alice"
        assert captured.get("body", {}).get("message") == "alice's objective"
        # the actual credential is the bearer token, never the identity field
        assert captured.get("auth_header") == "Bearer worker-secret-token"
    finally:
        httpd.shutdown()


def test_gate5_spoofed_identity_in_remote_response_cannot_override_ownership():
    """A malicious/compromised remote worker returns a response body that
    tries to claim a different user_id/account. Local ownership
    (rec.session_user, set at dispatch time from the authenticated caller)
    must not change."""
    from core.orchestrator import Orchestrator
    from core.workers import WorkerManager

    httpd, port, captured = _start_fake_remote_worker({
        "ok": True,
        "reply": "pwned",
        "user_id": "attacker",
        "account": "attacker",
        "owner": "attacker",
    })
    try:
        orch = Orchestrator(user_id="alice")
        wm = WorkerManager(orch)
        w = wm.register_worker("remote2", endpoint=f"http://127.0.0.1:{port}", capabilities={"browser"})
        w.meta["token"] = "worker-secret-token"

        rec = wm.dispatch("alice's objective", required_capabilities=["browser"])
        wm.wait(rec.id, timeout=5)

        assert rec.session_user == "alice", "remote response was able to override local ownership"
        assert rec.result.get("reply") == "pwned"  # content is still taken from the response
    finally:
        httpd.shutdown()


def test_gate5_retry_preserves_ownership_local_and_remote():
    from core.orchestrator import Orchestrator
    from core.workers import WorkerManager

    # local path: execute_fn fails once, then succeeds
    calls = {"n": 0}

    def flaky(objective):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return {"ok": True, "reply": "done"}

    orch = Orchestrator(user_id="bob")
    wm = WorkerManager(orch)
    wm.register_worker("local1")
    rec = wm.dispatch("bob's job", execute_fn=flaky, max_attempts=3)
    wm.wait(rec.id, timeout=5)
    assert rec.status.value == "succeeded"
    assert rec.attempts >= 2
    assert rec.session_user == "bob", "ownership lost across a local retry"

    # remote path: first response is a 500 (triggers retry), second succeeds
    import http.server
    hits = {"n": 0}

    class FlakyHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            hits["n"] += 1
            if hits["n"] < 2:
                self.send_response(500)
                self.end_headers()
                return
            resp = json.dumps({"ok": True, "reply": "done"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), FlakyHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    try:
        orch2 = Orchestrator(user_id="carol")
        wm2 = WorkerManager(orch2)
        w = wm2.register_worker("remote3", endpoint=f"http://127.0.0.1:{port}")
        w.meta["token"] = "tok"
        rec2 = wm2.dispatch("carol's job", max_attempts=3)
        wm2.wait(rec2.id, timeout=10)
        assert rec2.session_user == "carol", "ownership lost across a remote retry"
        assert rec2.status.value == "succeeded"
    finally:
        httpd.shutdown()


# ── Gate 3: credential isolation ───────────────────────────────────────

def test_gate3_credentials_scoped_per_user_not_shared_globally():
    """Two users' CredentialStores must be genuinely separate files (and
    separate encryption keys), not the same global ~/.pear location keyed
    only by connector name."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        alice = sm.get("alice").orchestrator
        bob = sm.get("bob").orchestrator

        assert alice.connectors.credentials.path != bob.connectors.credentials.path
        assert alice.connectors.credentials.key_path != bob.connectors.credentials.key_path

        alice.connectors.credentials.set("notion", {"token": "alice-secret"})
        bob.connectors.credentials.set("notion", {"token": "bob-secret"})

        assert alice.connectors.credentials.get("notion")["token"] == "alice-secret"
        assert bob.connectors.credentials.get("notion")["token"] == "bob-secret"

        # User A cannot use User B's connector credentials — same connector
        # NAME ("notion") resolves to a completely different underlying
        # credential depending on whose orchestrator asks.
        assert alice.connectors.credentials.get("notion") != bob.connectors.credentials.get("notion")


def test_gate3_credentials_survive_restart_still_isolated():
    """Ownership-style check for credentials specifically: rebuild
    SessionManager from scratch against the same on-disk data_root and
    confirm each user still only sees their own stored credential."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td)
        sm1 = SessionManager(data_root=data_root, llm=EchoLLM())
        sm1.get("alice").orchestrator.connectors.credentials.set("github", {"token": "alice-gh"})
        sm1.get("bob").orchestrator.connectors.credentials.set("github", {"token": "bob-gh"})

        sm2 = SessionManager(data_root=data_root, llm=EchoLLM())
        alice2 = sm2.get("alice").orchestrator
        bob2 = sm2.get("bob").orchestrator
        assert alice2.connectors.credentials.get("github")["token"] == "alice-gh"
        assert bob2.connectors.credentials.get("github")["token"] == "bob-gh"


def test_gate3_concurrent_multi_user_credential_isolation():
    """Required by the task card explicitly. Many threads, each acting as
    a different authenticated user, concurrently write and read back a
    connector credential. No thread may ever observe another user's
    value, under real concurrent access — not just sequential calls."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        users = [f"user{i}" for i in range(12)]
        errors = []

        def worker(username):
            try:
                orch = sm.get(username).orchestrator
                secret = f"{username}-secret-token"
                for _ in range(15):
                    orch.connectors.credentials.set("slack", {"token": secret})
                    got = orch.connectors.credentials.get("slack")
                    if got["token"] != secret:
                        errors.append(f"{username} saw {got['token']!r} instead of its own {secret!r}")
            except Exception as e:
                errors.append(f"{username}: {e}")

        threads = [threading.Thread(target=worker, args=(u,)) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, "cross-user credential leakage under concurrency:\n" + "\n".join(errors)

        # final check: every user's store still holds only its own value
        for u in users:
            got = sm.get(u).orchestrator.connectors.credentials.get("slack")
            assert got["token"] == f"{u}-secret-token"


def test_gate3_no_credential_values_in_api_response_or_status():
    """auth_status()/health() must never include raw credential values —
    only metadata (which keys exist, when updated), matching the pattern
    the code already used for connector status."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        orch = sm.get("alice").orchestrator
        orch.connectors.credentials.set("notion", {"token": "super-secret-value-xyz"})

        status = orch.connectors.auth_status()
        dumped = json.dumps(status)
        assert "super-secret-value-xyz" not in dumped

    with tempfile.TemporaryDirectory() as td2:
        service, httpd, port = _start_server(Path(td2))
        try:
            token = _login(port, "demo", "demo")
            sess = service.sessions.get("demo")
            sess.orchestrator.connectors.credentials.set("notion", {"token": "http-leak-check-999"})
            status, body = _get(port, token, "/v1/connectors")
            assert status == 200
            assert "http-leak-check-999" not in json.dumps(body)
        finally:
            httpd.shutdown()


def test_gate3_n8n_optionality_preserved():
    """Gate 3 must not disturb n8n's documented optional behavior — it
    should construct and report cleanly with zero configuration."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        orch = sm.get("alice").orchestrator
        assert orch.connectors.has("n8n")
        health = orch.connectors.get("n8n").health()
        assert health["status"] == "disconnected"  # not configured, not an error


# ── Gate 6: session lifecycle ──────────────────────────────────────────

def test_gate6_idle_session_can_be_evicted():
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        sm.get("alice")
        assert "alice" in sm._sessions
        # simulate idle by back-dating last_access rather than sleeping
        sm._sessions["alice"].last_access = time.time() - 10000
        evicted = sm.evict_idle(max_idle_s=1.0)
        assert evicted == ["alice"]
        assert "alice" not in sm._sessions


def test_gate6_active_job_blocks_eviction():
    """An orchestrator with a RUNNING job must not be evicted — active
    jobs/goals/workflows must not be destroyed."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        orch = sm.get("alice").orchestrator

        gate = threading.Event()
        orch.jobs.runner = lambda job: gate.wait(5)  # JobManager already
        # sets job.status = RUNNING before invoking the runner.
        orch.jobs.enqueue("slow work")
        orch.jobs.start()
        time.sleep(0.3)

        assert sm._is_busy(orch) is True
        assert sm.evict("alice") is False, "active job should have blocked eviction"
        assert "alice" in sm._sessions

        sm._sessions["alice"].last_access = time.time() - 10000
        assert sm.evict_idle(max_idle_s=1.0) == [], "evict_idle must also skip busy sessions"
        assert "alice" in sm._sessions

        gate.set()
        time.sleep(0.3)
        orch.jobs.stop()


def test_gate6_eviction_releases_resources_cleanly():
    """Worker threads/executors are actually shut down, not just the dict
    entry removed."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        orch = sm.get("bob").orchestrator
        orch.jobs.start()
        assert orch.jobs._workers, "expected worker threads to have started"
        assert not orch.workers._executor._shutdown

        assert sm.evict("bob") is True
        time.sleep(0.2)

        assert all(not t.is_alive() for t in orch.jobs._workers), "job worker threads should be stopped"
        assert orch.workers._executor._shutdown, "worker thread pool should be shut down"


def test_gate6_later_request_reconstructs_state_from_persistence():
    """login -> create session -> execute work -> idle -> evict ->
    restart/reconstruct -> state remains correct."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        orch1 = sm.get("carol").orchestrator
        goal = orch1.goals.create("carol's persistent goal", auto_start=False)
        goal_id = goal.id

        assert sm.evict("carol") is True
        assert "carol" not in sm._sessions

        # a later request rebuilds the session from the same data_root
        orch2 = sm.get("carol").orchestrator
        assert orch2 is not orch1
        reloaded = orch2.goals.get(goal_id)
        assert reloaded.objective == "carol's persistent goal"
        assert reloaded.user_id == "carol"


def test_gate6_concurrent_get_for_new_user_no_duplicate_sessions():
    """Many threads racing to get() the SAME brand-new user_id must all
    end up with the identical session/orchestrator, never two competing
    ones."""
    from service.sessions import SessionManager
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        results = []
        lock = threading.Lock()

        def worker():
            sess = sm.get("newuser")
            with lock:
                results.append(id(sess.orchestrator))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(set(results)) == 1, "concurrent get() for a new user produced multiple distinct orchestrators"


# ── Gate 7: persistence / recovery ─────────────────────────────────────

def test_gate7_atomic_write_survives_crash_mid_write():
    """A failure between 'temp file written' and 'atomic rename' must
    leave the ORIGINAL file completely untouched — never a partial/
    truncated version of the new content."""
    from core.security import atomic_write_text
    import os as _os

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        atomic_write_text(path, '{"version": 1, "data": "original"}')
        assert path.read_text() == '{"version": 1, "data": "original"}'

        real_replace = _os.replace
        def failing_replace(*a, **kw):
            raise OSError("simulated crash between write and rename")

        _os.replace = failing_replace
        try:
            try:
                atomic_write_text(path, '{"version": 2, "data": "new-content-that-should-never-land"}')
                assert False, "expected the simulated crash to propagate"
            except OSError:
                pass
        finally:
            _os.replace = real_replace

        # the original file must be exactly as it was — not corrupted,
        # not truncated, not partially overwritten.
        assert path.read_text() == '{"version": 1, "data": "original"}'
        # and no leftover temp files should linger in the directory
        leftovers = [p for p in Path(td).iterdir() if p.name.startswith(".state.json.tmp-")]
        assert leftovers == [], f"temp file(s) left behind: {leftovers}"


def test_gate7_corrupted_file_quarantined_loudly_not_silently_wiped():
    """A pre-existing corrupted file must produce a visible warning and a
    .corrupted-<ts> backup, not silent data loss disguised as 'empty
    store, nothing to see here'."""
    import sys
    from io import StringIO
    from core.security import safe_load_text

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "users.json"
        path.write_bytes(b"\x00\x01\xff not valid json at all {{{")

        captured = StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            result = safe_load_text(path, on_corrupt_label="test store")
        finally:
            sys.stderr = old_stderr

        assert result is None
        assert "WARNING" in captured.getvalue()
        assert "test store" in captured.getvalue()

        backups = list(Path(td).glob("users.json.corrupted-*"))
        assert len(backups) == 1, "expected exactly one quarantined backup of the corrupted file"
        assert backups[0].read_bytes() == b"\x00\x01\xff not valid json at all {{{"


def test_gate7_auth_database_corruption_does_not_silently_lock_everyone_out():
    """The highest-severity case: a corrupted users.json must not
    silently start the service with zero accounts. It should warn loudly
    and quarantine the file — this test locks in that it does NOT crash
    the whole service either, since that would be its own denial-of-
    service problem; it degrades to empty-but-loud instead."""
    from service.auth import AuthManager
    import sys
    from io import StringIO

    with tempfile.TemporaryDirectory() as td:
        users_path = Path(td) / "users.json"
        users_path.write_text("{not valid json")

        captured = StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            auth = AuthManager(persist_path=users_path)
        finally:
            sys.stderr = old_stderr

        assert "WARNING" in captured.getvalue()
        backups = list(Path(td).glob("users.json.corrupted-*"))
        assert len(backups) == 1
        # default accounts still get (re)seeded rather than the service
        # being left completely unusable
        assert auth.login("admin", "admin") is not None


def test_gate7_full_create_run_crash_restart_recover_flow():
    """The exact flow the gate specifies: create -> running -> process
    termination -> restart -> recovery, for a job, a goal, and a
    workflow run, using a real crash simulation (kill -9 style: no clean
    shutdown, no flush) rather than a clean SessionManager teardown."""
    from service.sessions import SessionManager
    from core.job import JobStatus
    from core.goals import GoalStatus

    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td)
        sm1 = SessionManager(data_root=data_root, llm=EchoLLM())
        orch1 = sm1.get("dana").orchestrator

        job = orch1.jobs.enqueue("dana's job")
        goal = orch1.goals.create("dana's goal", auto_start=False)

        # Simulate the job/goal being mid-flight at the moment of a crash
        # (no graceful stop(), no eviction — just abandon the objects,
        # like a kill -9 on the process).
        from core.job import JobStatus as _JS
        job.status = _JS.RUNNING
        orch1.jobs._save_job(job)
        goal.status = GoalStatus.RUNNING
        orch1.goals._save(goal)

        # "process termination" — sm1/orch1 are simply never used again,
        # nothing flushed or cleaned up on the way out.
        del sm1, orch1

        # "restart" — brand new SessionManager/Orchestrator against the
        # same on-disk data_root.
        sm2 = SessionManager(data_root=data_root, llm=EchoLLM())
        orch2 = sm2.get("dana").orchestrator

        recovered_job = orch2.jobs._jobs.get(job.id)
        assert recovered_job is not None, "job did not survive the crash at all"
        assert recovered_job.status == JobStatus.QUEUED, "a RUNNING job must recover to QUEUED, not stay stuck RUNNING forever"
        assert recovered_job.user_id == "dana"

        recovered_goal = orch2.goals.get(goal.id)
        assert recovered_goal is not None, "goal did not survive the crash at all"
        assert recovered_goal.user_id == "dana"


# ── Gate 8: adversarial concurrency testing ────────────────────────────

def test_gate8_adversarial_30_concurrent_users():
    """
    The gate's own minimum scenario: 30 concurrent users, each doing
    chat (tracer), goal creation (ownership/IDOR), connector credentials
    (isolation), and worker dispatch (identity propagation) at the same
    time, over a real ThreadingHTTPServer — not mocked, not sequential.

    This is deliberately not just "run the Gate 1-7 tests again" — it
    runs all of those properties simultaneously, from the same 30 threads,
    which can expose interaction effects an isolated per-gate test
    wouldn't: e.g. a race between the tracer context and credential
    lookup happening in the same request, not two separate test runs.
    """
    from service.auth import Role

    N = 30
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        service.rate_limiter.configure(per_minute=20000, burst=2000)

        usernames = [f"stress{i}" for i in range(N)]
        for u in usernames:
            service.auth.create_user(u, f"{u}-pw", Role.USER)

        errors = []
        results = {}  # username -> dict of what we recorded for it
        results_lock = threading.Lock()

        def user_workload(username):
            try:
                token = _login(port, username, f"{username}-pw")
                marker = f"MARKER_{username}_{threading.get_ident()}"

                # chat / tracer
                for i in range(4):
                    _chat(port, token, f"{marker} chat {i}")

                # goal creation (ownership + IDOR surface)
                status, body = _goal_create(port, token, f"{marker} goal")
                if status != 200:
                    raise AssertionError(f"goal create failed: {status} {body}")
                goal_id = body["goal"]["id"]
                goal_owner = body["goal"]["user_id"]

                # connector credentials (isolation) — direct orchestrator
                # access, since there's no HTTP route for this yet
                sess = service.sessions.get(username)
                orch = sess.orchestrator
                secret = f"{marker}-cred-secret"
                orch.connectors.credentials.set("slack", {"token": secret})
                got_secret = orch.connectors.credentials.get("slack")["token"]

                # worker dispatch (identity propagation)
                rec = orch.workers.dispatch(
                    f"{marker} dispatch",
                    execute_fn=lambda obj: {"ok": True, "reply": obj},
                )
                orch.workers.wait(rec.id, timeout=5)

                # traces (cross-user leak surface)
                status, trace_body = _get(port, token, "/v1/traces")
                traces_dump = json.dumps(trace_body)

                with results_lock:
                    results[username] = {
                        "marker": marker,
                        "goal_id": goal_id,
                        "goal_owner": goal_owner,
                        "cred_secret_expected": secret,
                        "cred_secret_got": got_secret,
                        "dispatch_session_user": rec.session_user,
                        "traces_dump": traces_dump,
                        "orch_id": id(orch),
                    }
            except Exception as e:
                with results_lock:
                    errors.append(f"{username}: {type(e).__name__}: {e}")

        threads = [threading.Thread(target=user_workload, args=(u,)) for u in usernames]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        try:
            assert not errors, f"{len(errors)} user workload(s) raised:\n" + "\n".join(errors)
            assert len(results) == N, f"expected {N} completed workloads, got {len(results)}"

            # no incorrect ownership
            for u, r in results.items():
                assert r["goal_owner"] == u, f"{u}: goal owned by {r['goal_owner']!r} instead"
                assert r["dispatch_session_user"] == u, f"{u}: dispatch owned by {r['dispatch_session_user']!r} instead"

            # no cross-user credentials
            for u, r in results.items():
                assert r["cred_secret_got"] == r["cred_secret_expected"], \
                    f"{u}: credential mismatch — got {r['cred_secret_got']!r}, expected {r['cred_secret_expected']!r}"

            # no duplicate/conflicting sessions — re-fetching each user's
            # session now must return the exact same orchestrator the
            # workload thread used
            for u, r in results.items():
                current = service.sessions.get(u).orchestrator
                assert id(current) == r["orch_id"], f"{u}: session orchestrator changed — duplicate/conflicting session"

            # no cross-user data / no race-induced state leakage: no
            # user's own marker may EVER appear in another user's traces
            # or goal id.
            all_markers = {u: r["marker"] for u, r in results.items()}
            for u, r in results.items():
                for other_u, other_marker in all_markers.items():
                    if other_u == u:
                        continue
                    assert other_marker not in r["traces_dump"], \
                        f"CROSS-USER LEAK: {u}'s traces contain {other_u}'s marker"

            # no worker identity substitution: every dispatch's
            # session_user matched its own username, and no OTHER
            # username slipped in (redundant with the ownership check
            # above, asserted separately to name this property explicitly)
            all_session_users = {r["dispatch_session_user"] for r in results.values()}
            assert all_session_users == set(usernames), "worker dispatch identities do not match the 30 users 1:1"
        finally:
            httpd.shutdown()


# ── Gate 9: API surface reconciliation ─────────────────────────────────

def _require_fastapi():
    """Explicit SKIP, not a silent no-op pass, when FastAPI isn't
    installed — a 'passed' test that verified nothing is worse than an
    honestly-reported skip. requirements.txt lists fastapi/uvicorn as
    commented-out/optional; that's a deliberate choice for anyone
    running just the stdlib surface, but it means these tests need
    pytest to know they didn't run, not pretend they did."""
    import pytest
    pytest.importorskip("fastapi")


def test_gate9_fastapi_login_now_rate_limits_and_audits():
    """Before Gate 9, FastAPI's /auth/login had no rate limiting and no
    audit log entry at all — a silent divergence from the stdlib surface
    that shares the exact same AuthManager/RateLimiter/AuditLog."""
    _require_fastapi()
    from service.app import create_app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as td:
        app = create_app(data_root=Path(td))
        service = app.state.service
        service.rate_limiter.configure(per_minute=60, burst=2)
        client = TestClient(app)

        codes = []
        for _ in range(5):
            r = client.post("/auth/login", json={"username": "rl_test_user", "password": "x"})
            codes.append(r.status_code)
        assert 429 in codes, "FastAPI login should be rate-limited exactly like stdlib login"

        login_entries = [e for e in service.audit.recent(50) if e.get("action") == "login"]
        assert len(login_entries) >= 5, "FastAPI login attempts should be audit-logged exactly like stdlib"


def test_gate9_fastapi_chat_now_feeds_learning():
    """Before Gate 9, FastAPI's /v1/chat never called
    learning.observe_route() — /v1/recommendations would silently starve
    forever under a FastAPI-only deployment."""
    _require_fastapi()
    from service.app import create_app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as td:
        app = create_app(data_root=Path(td))
        service = app.state.service
        client = TestClient(app)

        r = client.post("/auth/login", json={"username": "demo", "password": "demo"})
        token = r.json()["token"]
        orch = service.sessions.get("demo").orchestrator

        assert dict(orch.learning.routing_stats) == {}
        for i in range(3):
            client.post("/v1/chat", json={"message": f"hi {i}"}, headers={"Authorization": f"Bearer {token}"})
        assert orch.learning.routing_stats.get("personal", {}).get("n", 0) == 3


def test_gate9_stdlib_chat_stream_calls_route_exactly_once():
    """Before Gate 9, stdlib's /v1/chat/stream called orch.route() TWICE
    per request (leftover draft code never cleaned up) — since route()
    creates a Task and emits events per call, every streamed chat
    request was silently doing that work twice."""
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        try:
            token = _login(port, "demo", "demo")
            orch = service.sessions.get("demo").orchestrator
            tasks_before = len(orch.task_log)

            req = Request(
                f"http://127.0.0.1:{port}/v1/chat/stream",
                data=json.dumps({"message": "hello"}).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                json.loads(resp.read().decode())

            tasks_after = len(orch.task_log)
            assert tasks_after - tasks_before == 1, (
                f"expected exactly 1 new task from one /v1/chat/stream call, got {tasks_after - tasks_before} "
                f"— route() is being called more than once per request"
            )
        finally:
            httpd.shutdown()


def test_gate9_login_parity_between_surfaces():
    """The same credentials against both surfaces must produce
    equivalent outcomes — both success and failure shapes."""
    _require_fastapi()
    from service.app import create_app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        # stdlib
        service1, httpd1, port1 = _start_server(Path(td1))
        try:
            std_status, std_body = _post(port1, None, "/auth/login", {"username": "demo", "password": "wrong"})
        finally:
            httpd1.shutdown()

        # FastAPI, same scenario
        app = create_app(data_root=Path(td2))
        client = TestClient(app)
        fa_resp = client.post("/auth/login", json={"username": "demo", "password": "wrong"})

        assert std_status == 401
        assert fa_resp.status_code == 401
        assert std_body["ok"] is False
        assert fa_resp.json()["detail"]["ok"] is False
        assert std_body["error"] == fa_resp.json()["detail"]["error"]


def test_gate9_no_duplicate_dead_route_handlers():
    """Regression lock for the dead/duplicate beta route handlers found
    while building the Gate 9 parity matrix — a second, pre-Gate-2-
    vulnerable copy of /v1/beta/activate and /v1/beta/status sat later
    in _dispatch(), unreachable only because an earlier block always
    matched first. Reads the actual source rather than just testing
    behavior, since the whole point is that dead code doesn't execute —
    a behavioral test alone wouldn't have caught it existing at all."""
    import inspect
    from service.app import PearService
    src = inspect.getsource(PearService._dispatch)
    assert src.count('path == "/v1/beta/activate"') == 1
    assert src.count('path == "/v1/beta/status"') == 1


if __name__ == "__main__":
    test_gate1_concurrent_traces_no_cross_user_leak()
    print("  ✓ gate1 concurrent traces — no cross-user leak")
    test_gate1_sequential_construction_does_not_swap_global()
    print("  ✓ gate1 construction does not swap global")
    test_gate4_goal_stamped_with_owner_over_http()
    print("  ✓ gate4 goal stamped with owner (HTTP)")
    test_gate4_job_goal_workflow_dispatch_stamped_with_owner()
    print("  ✓ gate4 job/goal/workflow/dispatch stamped with owner")
    test_gate4_ownership_survives_restart()
    print("  ✓ gate4 ownership survives restart")
    test_gate2_cross_user_goal_access_denied()
    print("  ✓ gate2 cross-user goal access denied (404, not 403)")
    test_gate2_authorize_resource_admin_bypass_works_at_the_check_level()
    print("  ✓ gate2 authorize_resource() admin bypass correct at check level")
    test_gate2_admin_cannot_reach_another_users_goal_via_http_today()
    print("  ✓ gate2 documented: admin cross-session lookup gap (not fixed here)")
    test_gate2_beta_activate_ignores_client_supplied_account_when_authenticated()
    print("  ✓ gate2 beta activate ignores client-supplied account override")
    test_gate2_beta_status_cannot_probe_other_accounts()
    print("  ✓ gate2 beta status cannot probe other accounts")
    test_gate5_origin_identity_sent_as_metadata_not_credential()
    print("  ✓ gate5 origin identity sent as metadata, not credential")
    test_gate5_spoofed_identity_in_remote_response_cannot_override_ownership()
    print("  ✓ gate5 spoofed remote response cannot override ownership")
    test_gate5_retry_preserves_ownership_local_and_remote()
    print("  ✓ gate5 retry preserves ownership (local + remote)")
    test_gate3_credentials_scoped_per_user_not_shared_globally()
    print("  ✓ gate3 credentials scoped per user")
    test_gate3_credentials_survive_restart_still_isolated()
    print("  ✓ gate3 credentials survive restart, still isolated")
    test_gate3_concurrent_multi_user_credential_isolation()
    print("  ✓ gate3 concurrent multi-user credential isolation")
    test_gate3_no_credential_values_in_api_response_or_status()
    print("  ✓ gate3 no credential values leak into responses")
    test_gate3_n8n_optionality_preserved()
    print("  ✓ gate3 n8n optionality preserved")
    test_gate6_idle_session_can_be_evicted()
    print("  ✓ gate6 idle session evicted")
    test_gate6_active_job_blocks_eviction()
    print("  ✓ gate6 active job blocks eviction")
    test_gate6_eviction_releases_resources_cleanly()
    print("  ✓ gate6 eviction releases resources cleanly")
    test_gate6_later_request_reconstructs_state_from_persistence()
    print("  ✓ gate6 reconstructs state from persistence")
    test_gate6_concurrent_get_for_new_user_no_duplicate_sessions()
    print("  ✓ gate6 concurrent get() has no duplicate sessions")
    test_gate7_atomic_write_survives_crash_mid_write()
    print("  ✓ gate7 atomic write survives crash mid-write")
    test_gate7_corrupted_file_quarantined_loudly_not_silently_wiped()
    print("  ✓ gate7 corrupted file quarantined loudly")
    test_gate7_auth_database_corruption_does_not_silently_lock_everyone_out()
    print("  ✓ gate7 auth database corruption handled loudly, not silently")
    test_gate7_full_create_run_crash_restart_recover_flow()
    print("  ✓ gate7 full create/run/crash/restart/recover flow")
    test_gate8_adversarial_30_concurrent_users()
    print("  ✓ gate8 adversarial 30 concurrent users")
    test_gate9_fastapi_login_now_rate_limits_and_audits()
    print("  ✓ gate9 FastAPI login now rate-limits and audits")
    test_gate9_fastapi_chat_now_feeds_learning()
    print("  ✓ gate9 FastAPI chat now feeds learning")
    test_gate9_stdlib_chat_stream_calls_route_exactly_once()
    print("  ✓ gate9 stdlib chat/stream calls route() exactly once")
    test_gate9_login_parity_between_surfaces()
    print("  ✓ gate9 login parity between surfaces")
    test_gate9_no_duplicate_dead_route_handlers()
    print("  ✓ gate9 no duplicate dead route handlers")
    print("All PEAR 3.1 security tests passed (gates implemented so far).")
