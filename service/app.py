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
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


class PearService:
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
                data = json.loads(body.decode("utf-8"))
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
            user = self.auth.authenticate(data.get("username", ""), data.get("password", ""))
            if not user:
                self.audit.record("login", actor=data.get("username", ""), outcome="fail")
                return 401, {"ok": False, "error": "invalid credentials"}
            self.audit.record("login", actor=user.username, outcome="ok")
            return 200, {"ok": True, "token": user.token, "user": user.to_public()}

        # dashboard static
        if path in ("/", "/dashboard") and method == "GET":
            return 200, {"_html": (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")}

        user = self.user_from_headers(headers)
        if path.startswith("/admin"):
            self.auth.require(user, Role.ADMIN)

        if path == "/v1/me" and method == "GET":
            self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
            return 200, {"ok": True, "user": user.to_public()}

        # authenticated API
        self.auth.require(user, Role.USER, Role.ADMIN, Role.API_CLIENT)
        sess = self.sessions.get(user.username)
        orch = sess.orchestrator

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
            return 200, {"ok": True, "report": orch.goals.status_report(gid), "goal": orch.goals.get(gid).to_dict()}

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
                from core.tracing import get_tracer
                tr = get_tracer()
                traces = list(getattr(tr, "_traces", {}).keys())[-50:]
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
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.end_headers()
                return
            status, payload = service.handle_route(method, self.path, self._headers_dict(), self._read_body())
            if isinstance(payload, dict) and "_html" in payload:
                body = payload["_html"].encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
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
        user = service.auth.authenticate(payload.get("username", ""), payload.get("password", ""))
        if not user:
            raise HTTPException(401, "invalid credentials")
        return {"ok": True, "token": user.token, "user": user.to_public()}

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
