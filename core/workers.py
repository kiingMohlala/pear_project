"""
Distributed Worker Runtime (v2.30).

WorkerManager registers local/remote workers, routes by capability + load,
tracks heartbeats, retries, and timeouts. Planner/agents unchanged —
jobs and goals can dispatch eligible work transparently.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class WorkerStatus(str, Enum):
    ONLINE = "online"
    BUSY = "busy"
    DRAINING = "draining"
    DISABLED = "disabled"
    OFFLINE = "offline"


class DispatchStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    ACKED = "acked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RETRYING = "retrying"


# Capability tags
CAPABILITIES = {
    "general", "gpu", "browser", "desktop", "research",
    "finance", "legal", "email", "calendar", "computer", "media",
}


@dataclass
class WorkerInfo:
    id: str
    name: str
    capabilities: Set[str] = field(default_factory=lambda: {"general"})
    status: WorkerStatus = WorkerStatus.ONLINE
    endpoint: Optional[str] = None  # None = local
    max_concurrency: int = 2
    active_tasks: int = 0
    last_heartbeat: float = field(default_factory=time.time)
    total_completed: int = 0
    total_failed: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "capabilities": sorted(self.capabilities),
            "status": self.status.value,
            "endpoint": self.endpoint,
            "max_concurrency": self.max_concurrency,
            "active_tasks": self.active_tasks,
            "last_heartbeat": self.last_heartbeat,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
            "load": self.load,
            "meta": self.meta,
        }

    @property
    def load(self) -> float:
        if self.max_concurrency <= 0:
            return 1.0
        return self.active_tasks / self.max_concurrency

    @property
    def available(self) -> bool:
        return (
            self.status in (WorkerStatus.ONLINE, WorkerStatus.BUSY)
            and self.active_tasks < self.max_concurrency
        )


@dataclass
class DispatchRecord:
    id: str
    objective: str
    required_capabilities: List[str]
    worker_id: Optional[str] = None
    status: DispatchStatus = DispatchStatus.QUEUED
    result: Any = None
    error: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3
    timeout_s: float = 60.0
    created_at: float = field(default_factory=time.time)
    dispatched_at: Optional[float] = None
    finished_at: Optional[float] = None
    session_user: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "required_capabilities": self.required_capabilities,
            "worker_id": self.worker_id,
            "status": self.status.value,
            "result": self.result if not callable(self.result) else str(self.result),
            "error": self.error,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "timeout_s": self.timeout_s,
            "created_at": self.created_at,
            "dispatched_at": self.dispatched_at,
            "finished_at": self.finished_at,
            "session_user": self.session_user,
            "meta": self.meta,
        }


def infer_capabilities(objective: str) -> List[str]:
    obj = (objective or "").lower()
    caps: List[str] = []
    mapping = [
        (("browser", "web ", "http", "url", "scrape"), "browser"),
        (("desktop", "open app", "folder", "file "), "desktop"),
        (("research", "cite", "sources"), "research"),
        (("budget", "invoice", "finance", "transaction"), "finance"),
        (("contract", "nda", "legal", "clause"), "legal"),
        (("email", "inbox", "draft mail"), "email"),
        (("calendar", "schedule", "agenda", "meeting"), "calendar"),
        (("click", "gui", "computer use", "screenshot"), "computer"),
        (("transcribe", "ocr", "voice", "image"), "media"),
        (("gpu", "cuda", "model train"), "gpu"),
    ]
    for keys, cap in mapping:
        if any(k in obj for k in keys):
            caps.append(cap)
    if not caps:
        caps = ["general"]
    return caps


class WorkerManager:
    def __init__(
        self,
        orchestrator: Optional["Orchestrator"] = None,
        *,
        heartbeat_timeout_s: float = 30.0,
        max_workers_local: int = 4,
        persist_dir: Optional[Path] = None,
    ):
        self.orch = orchestrator
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.workers: Dict[str, WorkerInfo] = {}
        self.dispatches: Dict[str, DispatchRecord] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers_local, thread_name_prefix="pear-worker")
        self._futures: Dict[str, Future] = {}
        self.metrics = {
            "dispatched": 0,
            "succeeded": 0,
            "failed": 0,
            "timeouts": 0,
            "retries": 0,
            "dispatch_latency_ms_total": 0.0,
            "queue_time_ms_total": 0.0,
        }
        if persist_dir is None and orchestrator is not None:
            base = getattr(getattr(orchestrator, "memory", None), "persist_dir", None)
            persist_dir = Path(base) / "workers" if base else None
        self.persist_dir = Path(persist_dir) if persist_dir else Path.home() / ".pear" / "workers"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        # default local worker
        self.register_worker(
            name="local-default",
            capabilities={"general", "research", "finance", "legal", "email", "calendar", "media"},
            endpoint=None,
            max_concurrency=max_workers_local,
        )

    # ── tracing / events ─────────────────────────────────────────

    def _span(self, name: str, **attrs):
        try:
            from .tracing import get_tracer
            return get_tracer().span(name, kind="worker", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _emit(self, kind: str, **payload):
        if self.orch is None:
            return
        try:
            from .events import EventType
            self.orch.events.emit(EventType.NOTE, {"kind": kind, **payload}, source="workers")
        except Exception:
            pass

    # ── registration ──────────────────────────────────────────────

    def register_worker(
        self,
        name: str,
        capabilities: Optional[Set[str]] = None,
        *,
        endpoint: Optional[str] = None,
        max_concurrency: int = 2,
        worker_id: Optional[str] = None,
    ) -> WorkerInfo:
        with self._lock:
            wid = worker_id or f"wkr_{uuid.uuid4().hex[:10]}"
            caps = set(capabilities or {"general"})
            info = WorkerInfo(
                id=wid,
                name=name,
                capabilities=caps,
                endpoint=endpoint,
                max_concurrency=max_concurrency,
                status=WorkerStatus.ONLINE,
                last_heartbeat=time.time(),
            )
            self.workers[wid] = info
            self._emit("worker_registered", worker_id=wid, name=name, capabilities=sorted(caps))
            return info

    def heartbeat(self, worker_id: str, *, active_tasks: Optional[int] = None) -> WorkerInfo:
        with self._lock:
            w = self._require(worker_id)
            w.last_heartbeat = time.time()
            if active_tasks is not None:
                w.active_tasks = active_tasks
            if w.status == WorkerStatus.OFFLINE:
                w.status = WorkerStatus.ONLINE
            elif w.status not in (WorkerStatus.DISABLED, WorkerStatus.DRAINING):
                w.status = WorkerStatus.BUSY if w.active_tasks >= w.max_concurrency else WorkerStatus.ONLINE
            return w

    def enable(self, worker_id: str) -> WorkerInfo:
        with self._lock:
            w = self._require(worker_id)
            w.status = WorkerStatus.ONLINE
            w.last_heartbeat = time.time()
            self._emit("worker_enabled", worker_id=worker_id)
            return w

    def disable(self, worker_id: str) -> WorkerInfo:
        with self._lock:
            w = self._require(worker_id)
            w.status = WorkerStatus.DISABLED
            self._emit("worker_disabled", worker_id=worker_id)
            return w

    def quarantine(self, worker_id: str, reason: str = "repeated failures") -> WorkerInfo:
        with self._lock:
            w = self._require(worker_id)
            w.status = WorkerStatus.DISABLED
            w.meta["quarantined"] = True
            w.meta["quarantine_reason"] = reason
            w.meta["quarantined_at"] = time.time()
            self._emit("worker_quarantined", worker_id=worker_id, reason=reason)
            return w

    def maybe_quarantine(self, worker_id: str, threshold: int = 5) -> bool:
        """Quarantine worker if consecutive/total failures exceed threshold."""
        with self._lock:
            w = self._require(worker_id)
            fails = int(w.total_failed)
            if fails >= threshold and not w.meta.get("quarantined"):
                w.status = WorkerStatus.DISABLED
                w.meta["quarantined"] = True
                w.meta["quarantine_reason"] = f"failures>={threshold}"
                w.meta["quarantined_at"] = time.time()
                self._emit("worker_quarantined", worker_id=worker_id, failures=fails)
                return True
            return False

    def drain(self, worker_id: str) -> WorkerInfo:
        """Stop accepting new tasks; finish in-flight."""
        with self._lock:
            w = self._require(worker_id)
            w.status = WorkerStatus.DRAINING
            self._emit("worker_draining", worker_id=worker_id)
            return w

    def _require(self, worker_id: str) -> WorkerInfo:
        if worker_id not in self.workers:
            raise KeyError(f"Unknown worker: {worker_id}")
        return self.workers[worker_id]

    def check_heartbeats(self) -> List[str]:
        """Mark stale workers offline. Returns offline ids."""
        now = time.time()
        offline = []
        with self._lock:
            for w in self.workers.values():
                if w.status in (WorkerStatus.DISABLED,):
                    continue
                if w.endpoint is None:
                    # local workers always heartbeat via activity
                    w.last_heartbeat = now
                    continue
                if now - w.last_heartbeat > self.heartbeat_timeout_s:
                    w.status = WorkerStatus.OFFLINE
                    offline.append(w.id)
                    self._emit("worker_offline", worker_id=w.id)
        return offline

    # ── scheduling ────────────────────────────────────────────────

    def select_worker(self, required: List[str]) -> Optional[WorkerInfo]:
        self.check_heartbeats()
        req = set(required or ["general"])
        candidates = []
        with self._lock:
            for w in self.workers.values():
                if w.status in (WorkerStatus.DISABLED, WorkerStatus.OFFLINE, WorkerStatus.DRAINING):
                    continue
                if not w.available:
                    continue
                if not req.issubset(w.capabilities) and "general" not in w.capabilities:
                    # allow general workers for general-only requirements
                    if req != {"general"}:
                        continue
                if req.issubset(w.capabilities) or (
                    "general" in w.capabilities and req <= (w.capabilities | {"general"})
                ):
                    # prefer exact capability match
                    score = w.load
                    if req.issubset(w.capabilities):
                        score -= 0.01  # slight preference
                    candidates.append((score, w))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def dispatch(
        self,
        objective: str,
        *,
        required_capabilities: Optional[List[str]] = None,
        timeout_s: float = 60.0,
        max_attempts: int = 3,
        session_user: Optional[str] = None,
        execute_fn: Optional[Callable[[str], Any]] = None,
    ) -> DispatchRecord:
        caps = required_capabilities or infer_capabilities(objective)
        owner = session_user if session_user is not None else getattr(self.orch, "user_id", None)
        rec = DispatchRecord(
            id=f"disp_{uuid.uuid4().hex[:10]}",
            objective=objective,
            required_capabilities=caps,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
            session_user=owner,
        )
        with self._lock:
            self.dispatches[rec.id] = rec

        with self._span("worker.dispatch", dispatch_id=rec.id, caps=caps):
            return self._try_dispatch(rec, execute_fn=execute_fn)

    def _try_dispatch(self, rec: DispatchRecord, execute_fn: Optional[Callable] = None) -> DispatchRecord:
        worker = self.select_worker(rec.required_capabilities)
        if worker is None:
            rec.status = DispatchStatus.QUEUED
            rec.error = "no available worker"
            return rec

        rec.attempts += 1
        rec.worker_id = worker.id
        rec.status = DispatchStatus.DISPATCHED
        rec.dispatched_at = time.time()
        queue_ms = (rec.dispatched_at - rec.created_at) * 1000
        self.metrics["queue_time_ms_total"] += queue_ms
        self.metrics["dispatched"] += 1

        with self._lock:
            worker.active_tasks += 1
            if worker.active_tasks >= worker.max_concurrency:
                worker.status = WorkerStatus.BUSY

        self._emit("worker_dispatch", dispatch_id=rec.id, worker_id=worker.id)
        rec.status = DispatchStatus.ACKED

        if worker.endpoint:
            # remote HTTP dispatch
            fut = self._executor.submit(self._run_remote, rec, worker, execute_fn)
        else:
            fut = self._executor.submit(self._run_local, rec, worker, execute_fn)
        self._futures[rec.id] = fut
        return rec

    def _run_local(
        self,
        rec: DispatchRecord,
        worker: WorkerInfo,
        execute_fn: Optional[Callable],
    ) -> DispatchRecord:
        # PEAR 3.1 Gate 1: this runs inside a ThreadPoolExecutor worker
        # thread, which does NOT inherit the submitting thread's contextvars
        # (and pool threads are reused across dispatches from different
        # requests) — so the tracer must be activated here, not in dispatch().
        _tracer_token = None
        if self.orch is not None:
            from .tracing import set_tracer
            _tracer_token = set_tracer(self.orch.tracer)
        try:
            return self._run_local_inner(rec, worker, execute_fn)
        finally:
            if _tracer_token is not None:
                from .tracing import reset_tracer
                reset_tracer(_tracer_token)

    def _run_local_inner(
        self,
        rec: DispatchRecord,
        worker: WorkerInfo,
        execute_fn: Optional[Callable],
    ) -> DispatchRecord:
        rec.status = DispatchStatus.RUNNING
        t0 = time.time()
        try:
            if execute_fn:
                result = execute_fn(rec.objective)
            elif self.orch is not None:
                result = self.orch.route(rec.objective)
            else:
                result = {"ok": True, "reply": f"[worker {worker.name}] {rec.objective}"}
            # timeout check
            if time.time() - t0 > rec.timeout_s:
                raise TimeoutError(f"exceeded {rec.timeout_s}s")
            rec.result = result if not isinstance(result, dict) else result
            rec.status = DispatchStatus.SUCCEEDED
            rec.finished_at = time.time()
            self.metrics["succeeded"] += 1
            with self._lock:
                worker.total_completed += 1
        except Exception as e:
            rec.error = str(e)
            rec.finished_at = time.time()
            if "timed out" in str(e).lower() or isinstance(e, TimeoutError):
                rec.status = DispatchStatus.TIMEOUT
                self.metrics["timeouts"] += 1
            else:
                rec.status = DispatchStatus.FAILED
                self.metrics["failed"] += 1
            with self._lock:
                worker.total_failed += 1
            try:
                self.maybe_quarantine(worker.id)
            except Exception:
                pass
            # retry
            if rec.attempts < rec.max_attempts:
                rec.status = DispatchStatus.RETRYING
                self.metrics["retries"] += 1
                with self._lock:
                    worker.active_tasks = max(0, worker.active_tasks - 1)
                time.sleep(0.05 * rec.attempts)
                return self._try_dispatch(rec, execute_fn=execute_fn)
        finally:
            with self._lock:
                worker.active_tasks = max(0, worker.active_tasks - 1)
                if worker.status not in (WorkerStatus.DISABLED, WorkerStatus.DRAINING, WorkerStatus.OFFLINE):
                    worker.status = WorkerStatus.BUSY if worker.active_tasks >= worker.max_concurrency else WorkerStatus.ONLINE
                worker.last_heartbeat = time.time()
            latency = (time.time() - (rec.dispatched_at or t0)) * 1000
            self.metrics["dispatch_latency_ms_total"] += latency
            self._emit("worker_finished", dispatch_id=rec.id, status=rec.status.value)
        return rec

    def _run_remote(
        self,
        rec: DispatchRecord,
        worker: WorkerInfo,
        execute_fn: Optional[Callable],
    ) -> DispatchRecord:
        _tracer_token = None
        if self.orch is not None:
            from .tracing import set_tracer
            _tracer_token = set_tracer(self.orch.tracer)
        try:
            return self._run_remote_inner(rec, worker, execute_fn)
        finally:
            if _tracer_token is not None:
                from .tracing import reset_tracer
                reset_tracer(_tracer_token)

    def _run_remote_inner(
        self,
        rec: DispatchRecord,
        worker: WorkerInfo,
        execute_fn: Optional[Callable],
    ) -> DispatchRecord:
        """POST objective to remote worker endpoint (PEAR service /v1/chat)."""
        rec.status = DispatchStatus.RUNNING
        t0 = time.time()
        try:
            import urllib.request
            # PEAR 3.1 Gate 5: origin_user_id/dispatch_id are sent as
            # INFORMATIONAL metadata only, for the remote side's own audit
            # trail/tracing correlation — they are never treated as an
            # authentication credential. The worker's bearer token (below)
            # is the only thing that actually authenticates this request;
            # whoever that token belongs to on the remote side is who the
            # remote PEAR instance will authorize the request as. Sending
            # rec.session_user does not and must not grant it any
            # authority there.
            payload = json.dumps({
                "message": rec.objective,
                "dispatch_id": rec.id,
                "origin_user_id": rec.session_user,
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            token = worker.meta.get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = urllib.request.Request(
                worker.endpoint.rstrip("/") + "/v1/chat",
                data=payload,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=rec.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            # PEAR 3.1 Gate 5: rec.session_user was set locally, at dispatch
            # time, from the authenticated caller (Gate 4) — nothing from
            # the remote response is ever read into it here, deliberately.
            # A compromised or malicious remote worker returning a body
            # that happens to contain a "user_id"/"account"/similar key
            # cannot reassign local ownership of this dispatch; only the
            # objective's result content is taken from the response.
            rec.result = body
            rec.status = DispatchStatus.SUCCEEDED
            rec.finished_at = time.time()
            self.metrics["succeeded"] += 1
            with self._lock:
                worker.total_completed += 1
        except Exception as e:
            rec.error = str(e)
            rec.finished_at = time.time()
            rec.status = DispatchStatus.FAILED
            self.metrics["failed"] += 1
            with self._lock:
                worker.total_failed += 1
            if rec.attempts < rec.max_attempts:
                rec.status = DispatchStatus.RETRYING
                self.metrics["retries"] += 1
                with self._lock:
                    worker.active_tasks = max(0, worker.active_tasks - 1)
                return self._try_dispatch(rec, execute_fn=execute_fn)
        finally:
            with self._lock:
                worker.active_tasks = max(0, worker.active_tasks - 1)
                if worker.status not in (WorkerStatus.DISABLED, WorkerStatus.DRAINING, WorkerStatus.OFFLINE):
                    worker.status = WorkerStatus.ONLINE
                worker.last_heartbeat = time.time()
        return rec

    def wait(self, dispatch_id: str, timeout: Optional[float] = None) -> DispatchRecord:
        fut = self._futures.get(dispatch_id)
        if fut:
            try:
                fut.result(timeout=timeout)
            except Exception:
                pass
        return self.dispatches[dispatch_id]

    def list_workers(self) -> List[Dict[str, Any]]:
        self.check_heartbeats()
        return [w.to_dict() for w in self.workers.values()]

    def worker_status(self, worker_id: str) -> Dict[str, Any]:
        self.check_heartbeats()
        return self._require(worker_id).to_dict()

    def metrics_snapshot(self) -> Dict[str, Any]:
        d = dict(self.metrics)
        n = max(1, d["dispatched"])
        d["avg_dispatch_latency_ms"] = round(d["dispatch_latency_ms_total"] / n, 2)
        d["avg_queue_time_ms"] = round(d["queue_time_ms_total"] / n, 2)
        d["workers_online"] = sum(
            1 for w in self.workers.values()
            if w.status in (WorkerStatus.ONLINE, WorkerStatus.BUSY)
        )
        d["workers_total"] = len(self.workers)
        return d

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
