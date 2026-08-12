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


def _start_server(data_root: Path):
    service = PearService(data_root=data_root)
    service.sessions.llm = EchoLLM()
    # This suite is testing tracer isolation under concurrency, not rate
    # limiting — give it headroom so legitimate throttling doesn't masquerade
    # as a test failure under heavy parallel test-suite load.
    service.rate_limiter.configure(per_minute=6000, burst=500)
    handler = make_handler(service)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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
    print("All PEAR 3.1 security tests passed (gates implemented so far).")
