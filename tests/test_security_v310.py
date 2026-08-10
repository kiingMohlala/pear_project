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


if __name__ == "__main__":
    test_gate1_concurrent_traces_no_cross_user_leak()
    print("  ✓ gate1 concurrent traces — no cross-user leak")
    test_gate1_sequential_construction_does_not_swap_global()
    print("  ✓ gate1 construction does not swap global")
    print("All PEAR 3.1 security tests passed (gates implemented so far).")
