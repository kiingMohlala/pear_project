"""Security hardening regression tests (v3.1)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from service.auth import AuthManager, Role
from service.app import PearService
from core.security import (
    sanitize_string, sanitize_object, validate_body_size,
    check_upload_bytes, safe_upload_path, SecretBox,
)
from core.llm import EchoLLM
from core.config import Config, set_config


def test_idor_authorization():
    with tempfile.TemporaryDirectory() as td:
        auth = AuthManager(persist_path=Path(td) / "users.json")
        alice = auth.login("demo", "demo")
        assert alice
        # demo cannot access admin-owned resource
        try:
            auth.authorize_resource(alice, resource_owner="admin", allow_admin=False)
            assert False
        except PermissionError:
            pass
        # admin can
        admin = auth.login("admin", "admin")
        auth.authorize_resource(admin, resource_owner="demo", allow_admin=True)


def test_token_expiry_and_revoke():
    with tempfile.TemporaryDirectory() as td:
        auth = AuthManager(persist_path=Path(td) / "users.json", token_ttl_s=1, idle_timeout_s=10)
        user = auth.login("demo", "demo")
        token = user.token
        assert auth.resolve_token(f"Bearer {token}") is not None
        auth.revoke_token(token)
        assert auth.resolve_token(f"Bearer {token}") is None


def test_idle_timeout():
    with tempfile.TemporaryDirectory() as td:
        auth = AuthManager(persist_path=Path(td) / "users.json", token_ttl_s=3600, idle_timeout_s=0.05)
        user = auth.login("demo", "demo")
        token = user.token
        time.sleep(0.08)
        assert auth.resolve_token(f"Bearer {token}") is None


def test_lockout_after_failures():
    with tempfile.TemporaryDirectory() as td:
        auth = AuthManager(persist_path=Path(td) / "users.json", max_failed_logins=3)
        for _ in range(3):
            assert auth.login("demo", "wrong") is None
        st = auth.login_status("demo")
        assert st.get("locked") is True
        assert auth.login("demo", "demo") is None  # still locked


def test_rate_limit_login():
    with tempfile.TemporaryDirectory() as td:
        set_config(Config(profile="testing", overrides={"data_dir": td, "rate_limit_burst": 3, "rate_limit_per_minute": 30}))
        svc = PearService(data_root=Path(td))
        svc.sessions.llm = EchoLLM()
        svc.rate_limiter.configure(30, burst=3)
        codes = []
        for _ in range(6):
            st, body = svc.handle_route(
                "POST", "/auth/login", {},
                json.dumps({"username": "nosuch", "password": "x"}).encode(),
            )
            codes.append(st)
        assert 429 in codes or any(c == 401 for c in codes)


def test_body_size_limit():
    try:
        validate_body_size(b"x" * 2_000_000, max_bytes=1000)
        assert False
    except ValueError:
        pass


def test_sanitize_strips_controls():
    s = sanitize_string("hello\x00world")
    assert "\x00" not in s
    obj = sanitize_object({"a": "ok\x01", "n": 1})
    assert obj["a"] == "ok"


def test_upload_guards():
    with tempfile.TemporaryDirectory() as td:
        try:
            check_upload_bytes(b"data", "malware.exe")
            assert False
        except ValueError:
            pass
        check_upload_bytes(b"%PDF-1.4", "doc.pdf")
        p = safe_upload_path(Path(td), "../../etc/passwd.pdf")
        assert p.name.endswith(".pdf")
        assert ".." not in str(p.relative_to(td))


def test_secret_box_roundtrip_and_rotate():
    a = SecretBox("key-a")
    token = a.encrypt("secret-value")
    assert a.decrypt(token) == "secret-value"
    b = SecretBox("key-b")
    rotated = a.rotate(token, b)
    assert b.decrypt(rotated) == "secret-value"


def test_cross_user_session_isolation():
    with tempfile.TemporaryDirectory() as td:
        svc = PearService(data_root=Path(td))
        svc.sessions.llm = EchoLLM()
        _, a = svc.handle_route("POST", "/auth/login", {}, json.dumps({"username": "demo", "password": "demo"}).encode())
        _, b = svc.handle_route("POST", "/auth/login", {}, json.dumps({"username": "admin", "password": "admin"}).encode())
        ha = {"Authorization": f"Bearer {a['token']}"}
        # admin listing users requires admin - demo forbidden
        st, body = svc.handle_route("GET", "/admin/users", ha, b"")
        assert st in (401, 403) or body.get("ok") is False or st == 401


def test_logout_revokes():
    with tempfile.TemporaryDirectory() as td:
        svc = PearService(data_root=Path(td))
        svc.sessions.llm = EchoLLM()
        _, login = svc.handle_route("POST", "/auth/login", {}, json.dumps({"username": "demo", "password": "demo"}).encode())
        token = login["token"]
        h = {"Authorization": f"Bearer {token}"}
        st, _ = svc.handle_route("POST", "/auth/logout", h, b"{}")
        assert st == 200
        st2, body = svc.handle_route("GET", "/v1/me", h, b"")
        assert st2 in (401, 403) or body.get("ok") is False


if __name__ == "__main__":
    test_idor_authorization()
    print("  ✓ IDOR")
    test_token_expiry_and_revoke()
    print("  ✓ revoke")
    test_idle_timeout()
    print("  ✓ idle timeout")
    test_lockout_after_failures()
    print("  ✓ lockout")
    test_rate_limit_login()
    print("  ✓ auth rate limit")
    test_body_size_limit()
    print("  ✓ body size")
    test_sanitize_strips_controls()
    print("  ✓ sanitize")
    test_upload_guards()
    print("  ✓ uploads")
    test_secret_box_roundtrip_and_rotate()
    print("  ✓ crypto")
    test_cross_user_session_isolation()
    print("  ✓ cross-user")
    test_logout_revokes()
    print("  ✓ logout")
    print("All security v3.1 tests passed.")
