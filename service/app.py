"""
PEAR Service Layer (v2.20) – REST + WebSocket API.

Uses FastAPI when installed; otherwise falls back to stdlib http.server
for health/basic JSON routes (no WebSocket in fallback).
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .auth import AuthManager, Role
try:
    from core.beta import BetaManager
except Exception:  # optional — not shipped in some builds
    BetaManager = None  # type: ignore
from core.security import (
    sanitize_object, validate_body_size, DEFAULT_MAX_BODY,
    check_upload_bytes, safe_upload_path,
    apply_cors_headers, cors_allowed_origins,
)
from .sessions import SessionManager
from core.ratelimit import RateLimiter
from core.audit import AuditLog
from core.config import get_config
from core.logging_util import new_correlation_id, set_correlation_id, bind_context

START_TIME = time.time()
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    origin = handler.headers.get("Origin") or ""
    apply_cors_headers(handler.send_header, origin)
    handler.end_headers()
    handler.wfile.write(body)


class PearService:
    def _require_beta(self):
        if not self.beta:
            return 501, {"ok": False, "error": "beta program not installed in this build"}
        return None

    def __init__(self, data_root: Optional[Path] = None):
        root = Path(data_root) if data_root else Path(os.environ.get("PEAR_DATA", str(Path.home() / ".pear")))
        root.mkdir(parents=True, exist_ok=True)
        self.auth = AuthManager(persist_path=root / "users.json")
        self.sessions = SessionManager(data_root=root / "sessions")
        cfg = get_config()
        self.rate_limiter = RateLimiter(
            per_minute=int(cfg.get("rate_limit_per_minute", 120)),
            burst=int(cfg.get("rate_limit_burst", 30)),
        )
        self.audit = AuditLog(path=root / "audit.jsonl", enabled=bool(cfg.get("audit_enabled", True)))
        self.beta = BetaManager(persist_dir=root / "beta") if BetaManager else None
        self.metrics = {
            "requests": 0,
            "errors": 0,
            "routes": 0,
        }

    def user_from_headers(self, headers: Dict[str, str]):
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        return self.auth.resolve_token(auth)

    def handle_route(self, method: str, path: str, headers: Dict[str, str], body: bytes) -> tuple:
        self.metrics["requests"] += 1
        try:
            validate_body_size(body)
            cid = new_correlation_id()
            set_correlation_id(cid)
            user = self.user_from_headers(headers)
            key = (user.username if user else "anon") + ":" + path
            if path.startswith("/v1") or path.startswith("/admin"):
                ok, info = self.rate_limiter.allow(key)
                if not ok:
                    self.metrics["errors"] += 1
                    self.audit.record("rate_limited", actor=user.username if user else "anon", resource=path, outcome="deny", correlation_id=cid)
                    return 429, {"ok": False, "error": "rate limit exceeded", **info}
            return self._dispatch(method, path, headers, body)
        except PermissionError as e:
            self.metrics["errors"] += 1
            return 401, {"ok": False, "error": str(e)}
        except KeyError as e:
            self.metrics["errors"] += 1
            return 404, {"ok": False, "error": str(e)}
        except Exception as e:
            self.metrics["errors"] += 1
            return 500, {"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}

    def _dispatch(self, method: str, path: str, headers: Dict[str, str], body: bytes) -> tuple:
        parsed = urlparse(path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        data = {}
        if body:
            try:
                # size already checked
                data = sanitize_object(json.loads(body.decode("utf-8")))
            except Exception:
                data = {}

        # public
        if path == "/health" and method == "GET":
            return 200, {"status": "ok", "uptime_s": time.time() - START_TIME}
        if path == "/ready" and method == "GET":
            return 200, {"ready": True}
        if path == "/metrics" and method == "GET":
            return 200, {
                **self.metrics,
                "sessions": len(self.sessions.list_sessions()),
                "uptime_s": time.time() - START_TIME,
            }

        if path == "/auth/login" and method == "POST":
            username = str(data.get("username") or "")
            ok_rl, info = self.rate_limiter.allow(f"auth:{username or 'anon'}")
            if not ok_rl:
                self.audit.record("login", actor=username, outcome="rate_limited", detail=info)
                return 429, {"ok": False, "error": "too many login attempts", **info}
            status = self.auth.login_status(username)
            if status.get("locked"):
                self.audit.record("login", actor=username, outcome="locked", detail=status)
                return 423, {"ok": False, "error": "account temporarily locked", "retry_after_s": status.get("retry_after_s", 0)}
            user = self.auth.login(username, data.get("password") or "")
            if not user:
                self.audit.record("login", actor=username, outcome="fail")
                st = self.auth.login_status(username)
                return 401, {"ok": False, "error": "invalid credentials", "locked": st.get("locked"), "retry_after_s": st.get("retry_after_s", 0)}
            self.audit.record("login", actor=user.username, outcome="ok")
            return 200, {"ok": True, "token": user.token, "user": user.to_public(), "expires_hint_s": getattr(self.auth, "token_ttl_s", 43200)}

        if path == "/auth/logout" and method == "POST":
            # resolve user from header before require (user may not be set yet in this branch)
            user = self.user_from_headers(headers)
            self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
            tok = headers.get("Authorization") or headers.get("authorization") or ""
            self.auth.revoke_token(tok)
            self.audit.record("logout", actor=user.username, outcome="ok")
            return 200, {"ok": True}


        # dashboard static
        if path in ("/", "/dashboard") and method == "GET":
            return 200, {"_html": (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")}
        if path == "/beta" and method == "GET":
            p = STATIC_DIR / "beta_activate.html"
            if not p.exists() or not self.beta:
                return 404, {"ok": False, "error": "beta program not installed"}
            return 200, {"_html": p.read_text(encoding="utf-8")}
        if path == "/beta/feedback" and method == "GET":
            p = STATIC_DIR / "beta_feedback.html"
            if not p.exists() or not self.beta:
                return 404, {"ok": False, "error": "beta program not installed"}
            return 200, {"_html": p.read_text(encoding="utf-8")}
        if path == "/admin/beta" and method == "GET":
            p = STATIC_DIR / "beta_admin.html"
            if not p.exists() or not self.beta:
                return 404, {"ok": False, "error": "beta program not installed"}
            return 200, {"_html": p.read_text(encoding="utf-8")}
        if path == "/quant/paper" and method == "GET":
            return 200, {"_html": (STATIC_DIR / "quant_paper.html").read_text(encoding="utf-8")}

        user = self.user_from_headers(headers)
        if path.startswith("/admin"):
            self.auth.require(user, Role.ADMIN)

        if path == "/v1/me" and method == "GET":
            self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
            return 200, {"ok": True, "user": user.to_public()}

        # Beta activation is intentionally reachable without a PEAR login —
        # the invite code itself is the credential for a first-launch mobile
        # flow (see BetaKey.activate: rebinding an already-claimed code to a
        # different account/device is already rejected there).
        # PEAR 3.1 Gate 2: but if the caller IS authenticated, their own
        # identity must win — an authenticated user must never be able to
        # activate/check beta status for a different account by just naming
        # it in the request body. That was the actual bug: account =
        # data.get("account") OR user.username let a logged-in caller's
        # body override their own server-derived identity.
        if path == "/v1/beta/activate" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            account = user.username if user else (data.get("account") or "")
            if not account:
                return 400, {"ok": False, "error": "account required"}
            result = self.beta.activate(
                data.get("code") or "",
                account=account,
                device_id=str(data.get("device_id") or "unknown"),
                platform=str(data.get("platform") or "mobile"),
                app_version=str(data.get("app_version") or "3.0.0"),
            )
            self.audit.record(
                "beta_activate",
                actor=account,
                outcome="ok" if result.get("ok") else "fail",
                detail={"code_prefix": str(data.get("code") or "")[:9]},
            )
            return (200 if result.get("ok") else 400), result

        if path == "/v1/beta/status" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            # PEAR 3.1 Gate 2: this endpoint takes no credential at all (no
            # code, just account+device) — it was previously pure IDOR,
            # letting anyone probe any account's beta status by name.
            # Authenticated callers may only ever check their own account;
            # unauthenticated callers get a generic response with no
            # account-existence signal.
            if user is not None:
                account = user.username
            else:
                return 200, {"ok": False, "error": "no active beta license"}
            result = self.beta.check_access(
                account,
                str(data.get("device_id") or ""),
                platform=str(data.get("platform") or ""),
                app_version=str(data.get("app_version") or ""),
            )
            return 200, result

        # authenticated API
        self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
        sess = self.sessions.get(user.username)
        orch = sess.orchestrator
        # PEAR 3.1 Gate 1: activate this user's tracer for the rest of this
        # request. No explicit reset needed here — ThreadingHTTPServer gives
        # each request its own brand-new thread that terminates right after
        # (never pooled/reused), unlike the ThreadPoolExecutor paths in
        # JobManager/WorkerManager where an explicit reset is required.
        from core.tracing import set_tracer as _set_tracer
        _set_tracer(orch.tracer)


        if path == "/v1/upload" and method == "POST":
            # base64 body: {"filename": "...", "content_b64": "..."}
            import base64
            fname = str(data.get("filename") or "upload.bin")
            try:
                raw = base64.b64decode(data.get("content_b64") or "")
                check_upload_bytes(raw, fname)
                ws = Path(self.sessions.data_root) / user.username / "uploads"
                ws.mkdir(parents=True, exist_ok=True)
                dest = safe_upload_path(ws, fname)
                dest.write_bytes(raw)
            except Exception as e:
                self.audit.record("upload_denied", actor=user.username, outcome="fail", detail={"error": str(e)})
                return 400, {"ok": False, "error": str(e)}
            self.audit.record("upload", actor=user.username, outcome="ok", detail={"file": dest.name, "size": len(raw)})
            return 200, {"ok": True, "path": str(dest), "size": len(raw)}


        if path == "/v1/quant/paper/dashboard" and method == "GET":
            eng = getattr(self, "_paper_engine", None)
            if eng is None:
                return 200, {"ok": True, "active": 0, "rankings": [], "note": "no paper engine attached"}
            return 200, {"ok": True, **eng.dashboard_data()}

        if path == "/v1/chat" and method == "POST":
            message = data.get("message") or data.get("text") or ""
            self.metrics["routes"] += 1
            result = orch.route(message)
            # learning observe
            try:
                orch.learning.observe_route(
                    result.get("agent") or "unknown",
                    bool(result.get("ok")),
                    objective=message,
                )
            except Exception:
                pass
            return 200, {"ok": True, "result": result}

        if path == "/v1/chat/stream" and method == "POST":
            # stdlib fallback: return full response with streamed flag simulation
            message = data.get("message") or ""
            chunks = []
            result = orch.route(message, on_token=chunks.append) if "on_token" in getattr(orch.route, "__code__", type("", (), {"co_varnames": ()})).co_varnames else orch.route(message)
            # try streaming signature
            try:
                chunks = []
                result = orch.route(message, on_token=chunks.append)
            except TypeError:
                result = orch.route(message)
                chunks = list(result.get("reply") or "")
            return 200, {
                "ok": True,
                "result": result,
                "chunks": chunks if isinstance(chunks, list) else [],
                "streamed": True,
            }

        if path == "/v1/agents" and method == "GET":
            agents = []
            for name, agent in getattr(orch, "agents", {}).items():
                agents.append({
                    "name": name,
                    "description": getattr(agent, "description", ""),
                    "capabilities": list(getattr(agent, "capabilities", [])),
                })
            return 200, {"ok": True, "agents": agents}

        if path == "/v1/goals" and method == "GET":
            goals = [g.to_dict() for g in orch.goals.list_goals()]
            return 200, {"ok": True, "goals": goals}

        if path == "/v1/goals" and method == "POST":
            g = orch.goals.create(data.get("objective") or data.get("title") or "goal")
            return 200, {"ok": True, "goal": g.to_dict()}

        if path.startswith("/v1/goals/") and method == "GET":
            gid = path.split("/")[-1]
            try:
                goal = orch.goals.get(gid)
            except KeyError:
                return 404, {"ok": False, "error": f"unknown goal: {gid}"}
            # PEAR 3.1 Gate 2: explicit ownership check using the user_id
            # Gate 4 added to Goal, rather than relying only on the fact
            # that orch is already scoped to this user (structurally true
            # today, but this check keeps holding even if goal storage is
            # ever centralized/refactored later). Also finally gives
            # authorize_resource() a real caller instead of sitting dead.
            if goal.user_id is not None:
                try:
                    self.auth.authorize_resource(user, resource_owner=goal.user_id)
                except PermissionError:
                    return 404, {"ok": False, "error": f"unknown goal: {gid}"}
            return 200, {"ok": True, "report": orch.goals.status_report(gid), "goal": goal.to_dict()}

        if path == "/v1/jobs" and method == "GET":
            try:
                jobs = [j.to_dict() if hasattr(j, "to_dict") else str(j) for j in orch.jobs.list_jobs()]
            except Exception:
                jobs = []
            return 200, {"ok": True, "jobs": jobs}

        if path == "/v1/workflows" and method == "GET":
            try:
                wfs = list(getattr(orch.workflows, "definitions", {}) or {})
                if hasattr(orch.workflows, "list_workflows"):
                    wfs = orch.workflows.list_workflows()
            except Exception:
                wfs = []
            return 200, {"ok": True, "workflows": wfs}

        if path == "/v1/memories" and method == "GET":
            try:
                stats = orch.memory.memory_stats() if hasattr(orch.memory, "memory_stats") else {}
            except Exception:
                stats = {}
            return 200, {"ok": True, "stats": stats}

        if path == "/v1/traces" and method == "GET":
            try:
                traces = orch.tracer.list_traces(limit=50)
            except Exception:
                traces = []
            return 200, {"ok": True, "traces": traces}

        if path == "/v1/recommendations" and method == "GET":
            try:
                orch.learning.analyze()
                recs = orch.learning.list_recommendations()
            except Exception:
                recs = []
            return 200, {"ok": True, "recommendations": recs}

        if path == "/v1/plugins" and method == "GET":
            try:
                plugins = orch.plugins.list_plugins()
            except Exception:
                plugins = []
            return 200, {"ok": True, "plugins": plugins}

        if path == "/v1/connectors" and method == "GET":
            try:
                connectors = orch.connectors.list()
            except Exception:
                connectors = []
            return 200, {"ok": True, "connectors": connectors}

        if path == "/v1/evaluate" and method == "POST":
            from evaluation.engine import EvaluationEngine
            eng = EvaluationEngine()
            suites = data.get("suites")
            report = eng.run(suites=suites, save_history=True, compare_baseline=False)
            try:
                orch.learning.ingest_evaluation(report.to_dict())
            except Exception:
                pass
            return 200, {"ok": True, "report": report.to_dict()}


        # ── beta program ──────────────────────────────────────────
        if path == "/v1/beta/activate" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            account = data.get("account") or (user.username if user else "")
            if not account:
                return 400, {"ok": False, "error": "account required"}
            result = self.beta.activate(
                data.get("code") or "",
                account=account,
                device_id=str(data.get("device_id") or "unknown"),
                platform=str(data.get("platform") or "mobile"),
                app_version=str(data.get("app_version") or "3.0.0"),
            )
            self.audit.record(
                "beta_activate",
                actor=account,
                outcome="ok" if result.get("ok") else "fail",
                detail={"code_prefix": str(data.get("code") or "")[:9]},
            )
            return (200 if result.get("ok") else 400), result

        if path == "/v1/beta/status" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            account = data.get("account") or (user.username if user else "")
            result = self.beta.check_access(
                account or "",
                str(data.get("device_id") or ""),
                platform=str(data.get("platform") or ""),
                app_version=str(data.get("app_version") or ""),
            )
            return 200, result

        if path == "/v1/beta/consent" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
            self.beta.set_consent(user.username, bool(data.get("diagnostics")))
            return 200, {"ok": True, "diagnostics": self.beta.has_consent(user.username)}

        if path == "/v1/beta/feedback" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
            entry = self.beta.submit_feedback(
                user.username,
                data.get("message") or "",
                rating=int(data.get("rating") or 3),
                category=str(data.get("category") or "general"),
                include_diagnostics=bool(data.get("include_diagnostics")),
                diagnostics=data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else {},
                key_id=data.get("key_id"),
            )
            return 200, {"ok": True, "feedback": entry.to_dict()}

        if path == "/v1/beta/telemetry" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
            ev = self.beta.record_telemetry(
                user.username,
                str(data.get("event_type") or "error"),
                data.get("payload") if isinstance(data.get("payload"), dict) else {},
            )
            if ev is None:
                return 200, {"ok": False, "error": "diagnostics consent required"}
            return 200, {"ok": True, "event_id": ev.id}

        if path == "/admin/beta/keys" and method == "GET":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.ADMIN)
            return 200, {"ok": True, "keys": self.beta.list_keys(), "stats": self.beta.stats()}

        if path == "/admin/beta/keys" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.ADMIN)
            count = int(data.get("count") or 1)
            ttl = int(data.get("ttl_days") or 30)
            keys = self.beta.create_keys(count, ttl_days=ttl, label_prefix=str(data.get("label_prefix") or ""))
            self.audit.record("beta_create_keys", actor=user.username, detail={"count": count})
            return 200, {"ok": True, "keys": [k.to_dict() for k in keys]}

        if path == "/admin/beta/revoke" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.ADMIN)
            key = self.beta.revoke(data.get("key_id") or "", reason=str(data.get("reason") or "admin"))
            self.audit.record("beta_revoke", actor=user.username, resource=key.id)
            return 200, {"ok": True, "key": key.to_dict()}

        if path == "/admin/beta/extend" and method == "POST":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.ADMIN)
            key = self.beta.extend(data.get("key_id") or "", extra_days=int(data.get("days") or 30))
            return 200, {"ok": True, "key": key.to_dict()}

        if path == "/admin/beta/feedback" and method == "GET":
            _b = self._require_beta()
            if _b:
                return _b
            self.auth.require(user, Role.ADMIN)
            return 200, {"ok": True, "feedback": [f.to_dict() for f in self.beta.feedback[-100:]]}

        if path == "/admin/sessions" and method == "GET":
            return 200, {"ok": True, "sessions": self.sessions.list_sessions()}

        if path == "/admin/users" and method == "GET":
            return 200, {"ok": True, "users": [u.to_public() for u in self.auth.users.values()]}

        return 404, {"ok": False, "error": f"not found: {method} {path}"}


def make_handler(service: PearService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quieter tests

        def _headers_dict(self):
            return {k: v for k, v in self.headers.items()}

        def _read_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _handle(self, method: str):
            if method == "OPTIONS":
                self.send_response(204)
                origin = self.headers.get("Origin") or ""
                apply_cors_headers(self.send_header, origin)
                self.end_headers()
                return
            status, payload = service.handle_route(method, self.path, self._headers_dict(), self._read_body())
            if isinstance(payload, dict) and "_html" in payload:
                body = payload["_html"].encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                apply_cors_headers(self.send_header, self.headers.get("Origin") or "")
                self.end_headers()
                self.wfile.write(body)
                return
            _json_response(self, status, payload)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_OPTIONS(self):
            self._handle("OPTIONS")

    return Handler


def create_app(data_root: Optional[Path] = None):
    """Return FastAPI app when available, else None (use run_stdlib)."""
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
        from fastapi.middleware.cors import CORSMiddleware
    except Exception:
        return None

    service = PearService(data_root=data_root)
    app = FastAPI(title="PEAR API", version="2.20")
    origins = cors_allowed_origins()
    # FastAPI: empty list would block all; use same-origin only by not reflecting *
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins else [],
        allow_credentials=bool(origins) and origins != ["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "Accept", "Origin"],
        max_age=600,
    )
    app.state.service = service

    def user_dep(authorization: Optional[str] = Header(None)):
        user = service.auth.resolve_token(authorization or "")
        if not user:
            raise HTTPException(401, "authentication required")
        return user

    @app.get("/health")
    def health():
        return {"status": "ok", "uptime_s": time.time() - START_TIME}

    @app.get("/ready")
    def ready():
        return {"ready": True}

    @app.get("/metrics")
    def metrics():
        return {**service.metrics, "sessions": len(service.sessions.list_sessions())}

    @app.post("/auth/login")
    def login(payload: dict):
        username = str(payload.get("username") or "")
        st = service.auth.login_status(username)
        if st.get("locked"):
            raise HTTPException(423, "account temporarily locked")
        user = service.auth.login(username, payload.get("password") or "")
        if not user:
            raise HTTPException(401, "invalid credentials")
        return {"ok": True, "token": user.token, "user": user.to_public()}

    @app.post("/auth/logout")
    def logout(authorization: Optional[str] = Header(None)):
        user = user_dep(authorization)
        service.auth.revoke_token(authorization or "")
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        return (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")

    @app.post("/v1/chat")
    def chat(payload: dict, authorization: Optional[str] = Header(None)):
        user = user_dep(authorization)
        orch = service.sessions.get(user.username).orchestrator
        result = orch.route(payload.get("message") or "")
        return {"ok": True, "result": result}

    @app.post("/v1/chat/stream")
    def chat_stream(payload: dict, authorization: Optional[str] = Header(None)):
        user = user_dep(authorization)
        orch = service.sessions.get(user.username).orchestrator
        message = payload.get("message") or ""

        def gen():
            chunks = []
            try:
                result = orch.route(message, on_token=lambda t: chunks.append(t) or None)
            except TypeError:
                result = orch.route(message)
            # emit chunks then final
            text = result.get("reply") or ""
            # if no token callback captured, chunk the reply
            parts = chunks or [text[i:i+32] for i in range(0, len(text), 32)]
            for p in parts:
                yield f"data: {json.dumps({'token': p})}\n\n"
            yield f"data: {json.dumps({'done': True, 'result': result})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v1/goals")
    def list_goals(authorization: Optional[str] = Header(None)):
        user = user_dep(authorization)
        orch = service.sessions.get(user.username).orchestrator
        return {"ok": True, "goals": [g.to_dict() for g in orch.goals.list_goals()]}

    @app.get("/v1/agents")
    def list_agents(authorization: Optional[str] = Header(None)):
        user = user_dep(authorization)
        orch = service.sessions.get(user.username).orchestrator
        return {
            "ok": True,
            "agents": [
                {"name": n, "description": getattr(a, "description", ""), "capabilities": list(getattr(a, "capabilities", []))}
                for n, a in getattr(orch, "agents", {}).items()
            ],
        }

    @app.get("/v1/recommendations")
    def recommendations(authorization: Optional[str] = Header(None)):
        user = user_dep(authorization)
        orch = service.sessions.get(user.username).orchestrator
        try:
            orch.learning.analyze()
            recs = orch.learning.list_recommendations()
        except Exception:
            recs = []
        return {"ok": True, "recommendations": recs}

    return app


def run_stdlib(host: str = "0.0.0.0", port: int = 8080, data_root: Optional[Path] = None):
    service = PearService(data_root=data_root)
    handler = make_handler(service)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"PEAR service (stdlib) on http://{host}:{port}")
    httpd.serve_forever()


def main():
    host = os.environ.get("PEAR_HOST", "0.0.0.0")
    port = int(os.environ.get("PEAR_PORT", "8080"))
    data = os.environ.get("PEAR_DATA")
    data_root = Path(data) if data else None
    app = create_app(data_root=data_root)
    if app is not None:
        try:
            import uvicorn
            uvicorn.run(app, host=host, port=port)
            return
        except Exception:
            pass
    run_stdlib(host=host, port=port, data_root=data_root)


if __name__ == "__main__":
    main()
