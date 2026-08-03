"""
Optional n8n connector (v2.35).

Disabled by default. Uses webhook + REST API when configured.
No hard dependency on the n8n package — plain HTTP (or injectable mock client).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class N8NConnector(Connector):
    name = "n8n"
    description = "Optional integration with external n8n workflow automation"
    provider = "n8n"
    capabilities = [
        ConnectorCapability("list_workflows", "List n8n workflows", "n8n_read"),
        ConnectorCapability("execute_workflow", "Run an n8n workflow", "n8n_execute", sensitive=True),
        ConnectorCapability("get_execution_status", "Get execution status", "n8n_read"),
        ConnectorCapability("cancel_execution", "Cancel a running execution", "n8n_execute", sensitive=True),
        ConnectorCapability("list_executions", "List recent executions", "n8n_read"),
        ConnectorCapability("handle_callback", "Inbound n8n webhook callback", "n8n_webhook"),
    ]

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        *,
        timeout_s: float = 30.0,
        http_client: Optional[Callable[..., Any]] = None,
    ):
        super().__init__()
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout_s = timeout_s
        # injectible for offline tests: fn(method, url, headers, body) -> dict
        self.http_client = http_client
        self.orchestrator: Any = None
        self.events: Any = None
        self._callback_handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {}
        self._executions: Dict[str, Dict[str, Any]] = {}

    # ── lifecycle ─────────────────────────────────────────────────

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or {}
        if creds.get("base_url"):
            self.base_url = str(creds["base_url"]).rstrip("/")
        if creds.get("api_key") or creds.get("token"):
            self.api_key = str(creds.get("api_key") or creds.get("token"))
        if not self.base_url:
            self.status = ConnectorStatus.DISCONNECTED
            return ConnectorResult(
                ok=False,
                error="n8n base_url not configured (connector disabled by default)",
            )
        self.status = ConnectorStatus.CONNECTING
        self.connected_at = time.time()
        self.status = ConnectorStatus.CONNECTED
        return ConnectorResult(ok=True, message=f"n8n ready at {self.base_url}")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or {}
        if creds.get("api_key") or creds.get("token"):
            self.api_key = str(creds.get("api_key") or creds.get("token"))
        if creds.get("base_url"):
            self.base_url = str(creds["base_url"]).rstrip("/")
        if not self.base_url:
            self.status = ConnectorStatus.AUTH_REQUIRED
            return ConnectorResult(ok=False, error="missing base_url")
        if not self.api_key:
            # allow unauthenticated local/mock instances
            self.status = ConnectorStatus.CONNECTED
            return ConnectorResult(ok=True, message="n8n connected without API key")
        self.status = ConnectorStatus.CONNECTED
        return ConnectorResult(ok=True, message="n8n authenticated")

    def disconnect(self) -> ConnectorResult:
        self.status = ConnectorStatus.DISCONNECTED
        self.connected_at = None
        return ConnectorResult(ok=True, message="n8n disconnected")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action in ("status", "is_enabled"):
            return ConnectorResult(
                ok=True,
                data={
                    "enabled": bool(self.base_url) and self.status == ConnectorStatus.CONNECTED,
                    "base_url": self.base_url or None,
                    "status": self.status.value,
                },
            )
        if not self.base_url and action != "handle_callback":
            return ConnectorResult(ok=False, error="n8n connector disabled (no base_url)")
        actions = {
            "list_workflows": self.list_workflows,
            "execute_workflow": self.execute_workflow,
            "get_execution_status": self.get_execution_status,
            "cancel_execution": self.cancel_execution,
            "list_executions": self.list_executions,
            "handle_callback": self.handle_callback,
        }
        fn = actions.get(action)
        if not fn:
            return ConnectorResult(ok=False, error=f"unknown action: {action}")
        return fn(**params)

    # ── HTTP ──────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-N8N-API-KEY"] = self.api_key
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = self._headers()
        if self.http_client is not None:
            return self.http_client(method, url, headers, body)

        payload = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(url, data=payload, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else str(e)
            raise RuntimeError(f"n8n HTTP {e.code}: {err_body[:300]}") from e
        except URLError as e:
            raise RuntimeError(f"n8n unreachable: {e}") from e

    def _span(self, name: str, **attrs):
        try:
            from core.tracing import get_tracer
            return get_tracer().span(name, kind="connector", connector="n8n", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _emit(self, kind: str, **payload):
        try:
            from core.events import EventType
            if self.events is not None:
                self.events.emit(EventType.NOTE, {"kind": kind, **payload}, source="n8n")
        except Exception:
            pass

    # ── actions ───────────────────────────────────────────────────

    def list_workflows(self, **_) -> ConnectorResult:
        with self._span("n8n.list_workflows"):
            data = self._request("GET", "/api/v1/workflows")
            workflows = data.get("data") or data.get("workflows") or data
            if not isinstance(workflows, list):
                workflows = []
            self._emit("n8n_list_workflows", count=len(workflows))
            return ConnectorResult(ok=True, data={"workflows": workflows})

    def execute_workflow(
        self,
        workflow_id: str = "",
        workflow: str = "",
        data: Optional[dict] = None,
        **_,
    ) -> ConnectorResult:
        wid = workflow_id or workflow
        if not wid:
            return ConnectorResult(ok=False, error="workflow_id required")
        with self._span("n8n.execute_workflow", workflow_id=str(wid)):
            body = data or {}
            if str(wid).startswith("webhook:") or str(wid).startswith("/webhook"):
                path = str(wid).replace("webhook:", "")
                if not path.startswith("/"):
                    path = "/webhook/" + path
                result = self._request("POST", path, body)
            else:
                result = self._request("POST", f"/api/v1/workflows/{wid}/run", body)
            exec_id = (
                str(
                    result.get("id")
                    or result.get("executionId")
                    or (result.get("data") or {}).get("executionId")
                    or f"exec_{uuid.uuid4().hex[:10]}"
                )
            )
            self._executions[exec_id] = {
                "id": exec_id,
                "workflow_id": wid,
                "status": result.get("status") or "running",
                "started_at": time.time(),
                "result": result,
            }
            self._emit("n8n_execute", workflow_id=str(wid), execution_id=exec_id)
            return ConnectorResult(
                ok=True,
                data={"execution_id": exec_id, "workflow_id": wid, "raw": result},
            )

    def get_execution_status(self, execution_id: str = "", **_) -> ConnectorResult:
        if not execution_id:
            return ConnectorResult(ok=False, error="execution_id required")
        with self._span("n8n.get_execution_status", execution_id=execution_id):
            if execution_id in self._executions:
                return ConnectorResult(ok=True, data=self._executions[execution_id])
            data = self._request("GET", f"/api/v1/executions/{execution_id}")
            status = data.get("status") or ("success" if data.get("finished") else "unknown")
            out = {"id": execution_id, "status": status, "raw": data}
            self._executions[execution_id] = out
            return ConnectorResult(ok=True, data=out)

    def cancel_execution(self, execution_id: str = "", **_) -> ConnectorResult:
        if not execution_id:
            return ConnectorResult(ok=False, error="execution_id required")
        with self._span("n8n.cancel_execution", execution_id=execution_id):
            try:
                data = self._request("POST", f"/api/v1/executions/{execution_id}/stop", {})
            except Exception:
                data = {"stopped": True}
            if execution_id in self._executions:
                self._executions[execution_id]["status"] = "cancelled"
            self._emit("n8n_cancel", execution_id=execution_id)
            return ConnectorResult(
                ok=True,
                data={"execution_id": execution_id, "status": "cancelled", "raw": data},
            )

    def list_executions(self, limit: int = 20, **_) -> ConnectorResult:
        with self._span("n8n.list_executions"):
            try:
                data = self._request("GET", f"/api/v1/executions?limit={int(limit)}")
                items = data.get("data") or data.get("results") or data
                if not isinstance(items, list):
                    items = list(self._executions.values())
            except Exception:
                items = list(self._executions.values())[-int(limit):]
            return ConnectorResult(ok=True, data={"executions": items[: int(limit)]})

    def register_callback(self, key: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._callback_handlers[key] = handler

    def handle_callback(self, payload: Optional[dict] = None, **kwargs) -> ConnectorResult:
        data = dict(payload or {})
        data.update({k: v for k, v in kwargs.items() if k != "payload"})
        with self._span("n8n.callback"):
            self._emit("n8n_callback", keys=list(data.keys()))
            resumed: List[str] = []
            goal_id = data.get("goal_id")
            job_id = data.get("job_id")
            orch = self.orchestrator
            if orch is not None and goal_id:
                try:
                    orch.goals.resume(str(goal_id))
                    resumed.append(f"goal:{goal_id}")
                except Exception as e:
                    return ConnectorResult(ok=False, error=f"goal resume failed: {e}", data=data)
            if orch is not None and job_id:
                try:
                    if hasattr(orch.jobs, "resume"):
                        orch.jobs.resume(str(job_id))
                        resumed.append(f"job:{job_id}")
                except Exception:
                    pass
            for key, handler in self._callback_handlers.items():
                try:
                    handler(data)
                    resumed.append(f"handler:{key}")
                except Exception:
                    pass
            exec_id = data.get("execution_id") or data.get("id")
            if exec_id:
                self._executions[str(exec_id)] = {
                    "id": str(exec_id),
                    "status": data.get("status") or "success",
                    "callback": data,
                    "finished_at": time.time(),
                }
            return ConnectorResult(ok=True, data={"resumed": resumed, "payload": data})
