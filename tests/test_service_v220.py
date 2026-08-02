"""Service layer regression tests (v2.20)."""

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

from service.auth import AuthManager, Role, hash_password
from service.sessions import SessionManager
from service.app import PearService, make_handler
from http.server import ThreadingHTTPServer
from core.llm import EchoLLM


def test_auth_roles_and_tokens():
    with tempfile.TemporaryDirectory() as td:
        auth = AuthManager(persist_path=Path(td) / "users.json")
        u = auth.authenticate("demo", "demo")
        assert u and u.role == Role.USER
        assert auth.resolve_token(u.token).username == "demo"
        assert auth.resolve_token("bad") is None
        admin = auth.authenticate("admin", "admin")
        assert admin.role == Role.ADMIN


def test_session_isolation():
    with tempfile.TemporaryDirectory() as td:
        sm = SessionManager(data_root=Path(td), llm=EchoLLM())
        a = sm.get("alice")
        b = sm.get("bob")
        assert a.user_id != b.user_id
        assert a.orchestrator is not b.orchestrator
        a.orchestrator.memory.add("user", "alice secret")
        # bob should not see alice working memory
        bob_hist = b.orchestrator.memory.get_history()
        assert not any("alice secret" in getattr(m, "content", "") for m in bob_hist)


def test_service_health_login_chat():
    with tempfile.TemporaryDirectory() as td:
        service = PearService(data_root=Path(td))
        # force echo-friendly sessions
        service.sessions.llm = EchoLLM()
        status, health = service.handle_route("GET", "/health", {}, b"")
        assert status == 200 and health["status"] == "ok"
        status, ready = service.handle_route("GET", "/ready", {}, b"")
        assert status == 200 and ready["ready"] is True
        status, metrics = service.handle_route("GET", "/metrics", {}, b"")
        assert status == 200 and "requests" in metrics

        status, login = service.handle_route(
            "POST", "/auth/login", {},
            json.dumps({"username": "demo", "password": "demo"}).encode(),
        )
        assert status == 200 and login["ok"]
        token = login["token"]
        headers = {"Authorization": f"Bearer {token}"}
        status, chat = service.handle_route(
            "POST", "/v1/chat", headers,
            json.dumps({"message": "hello"}).encode(),
        )
        assert status == 200 and chat.get("ok")

        status, agents = service.handle_route("GET", "/v1/agents", headers, b"")
        assert status == 200 and agents.get("agents")

        # unauthorized
        status, err = service.handle_route("GET", "/v1/agents", {}, b"")
        assert status == 401


def test_concurrent_users():
    with tempfile.TemporaryDirectory() as td:
        service = PearService(data_root=Path(td))
        service.sessions.llm = EchoLLM()
        tokens = {}
        for name in ("demo", "admin"):
            pw = "demo" if name == "demo" else "admin"
            _, login = service.handle_route(
                "POST", "/auth/login", {},
                json.dumps({"username": name, "password": pw}).encode(),
            )
            tokens[name] = login["token"]

        def chat(user, msg):
            h = {"Authorization": f"Bearer {tokens[user]}"}
            return service.handle_route(
                "POST", "/v1/chat", h,
                json.dumps({"message": msg}).encode(),
            )

        results = []
        threads = [
            threading.Thread(target=lambda: results.append(chat("demo", "note: from demo"))),
            threading.Thread(target=lambda: results.append(chat("admin", "note: from admin"))),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 2
        assert all(s == 200 for s, _ in results)


def test_streaming_endpoint():
    with tempfile.TemporaryDirectory() as td:
        service = PearService(data_root=Path(td))
        service.sessions.llm = EchoLLM()
        _, login = service.handle_route(
            "POST", "/auth/login", {},
            json.dumps({"username": "demo", "password": "demo"}).encode(),
        )
        headers = {"Authorization": f"Bearer {login['token']}"}
        status, payload = service.handle_route(
            "POST", "/v1/chat/stream", headers,
            json.dumps({"message": "hello stream"}).encode(),
        )
        assert status == 200
        assert payload.get("streamed") is True


def test_dashboard_html():
    with tempfile.TemporaryDirectory() as td:
        service = PearService(data_root=Path(td))
        status, payload = service.handle_route("GET", "/dashboard", {}, b"")
        assert status == 200
        assert "_html" in payload and "PEAR" in payload["_html"]


def test_http_server_startup():
    with tempfile.TemporaryDirectory() as td:
        service = PearService(data_root=Path(td))
        service.sessions.llm = EchoLLM()
        handler = make_handler(service)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.15)
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                data = json.loads(resp.read().decode())
                assert data["status"] == "ok"
            # login + chat over real HTTP
            req = Request(
                f"http://127.0.0.1:{port}/auth/login",
                data=json.dumps({"username": "demo", "password": "demo"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                login = json.loads(resp.read().decode())
                assert login["ok"]
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    test_auth_roles_and_tokens()
    print("  ✓ auth")
    test_session_isolation()
    print("  ✓ session isolation")
    test_service_health_login_chat()
    print("  ✓ health/login/chat")
    test_concurrent_users()
    print("  ✓ concurrent users")
    test_streaming_endpoint()
    print("  ✓ streaming")
    test_dashboard_html()
    print("  ✓ dashboard")
    test_http_server_startup()
    print("  ✓ http startup")
    print("All v2.20 service tests passed.")
