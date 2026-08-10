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


def _goal_create(port: int, token: str, objective: str) -> dict:
    req = Request(
        f"http://127.0.0.1:{port}/v1/goals",
        data=json.dumps({"objective": objective}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


# ── Gate 4: explicit ownership propagation ───────────────────────────

def test_gate4_goal_stamped_with_owner_over_http():
    with tempfile.TemporaryDirectory() as td:
        service, httpd, port = _start_server(Path(td))
        try:
            token_demo = _login(port, "demo", "demo")
            token_admin = _login(port, "admin", "admin")

            g_demo = _goal_create(port, token_demo, "demo's private goal")
            g_admin = _goal_create(port, token_admin, "admin's private goal")

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
    print("All PEAR 3.1 security tests passed (gates implemented so far).")
