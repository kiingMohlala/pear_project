"""
Execution tracing & observability (v0.34).

Every request/job gets a root Trace; nested Spans cover planner, retrieval,
LLM, agents, tools, vector search, and job execution.

Agents stay unchanged — instrumentation lives in orchestrator, jobs, memory,
tools, and LLM wrappers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Span:
    name: str
    id: str = field(default_factory=lambda: _new_id("span"))
    trace_id: str = ""
    parent_id: Optional[str] = None
    kind: str = "internal"  # request | planner | retrieval | llm | agent | tool | vector | job
    status: str = "ok"  # ok | error
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at) * 1000, 3)

    def end(self, status: str = "ok", error: Optional[str] = None, **attrs: Any) -> None:
        self.ended_at = time.time()
        self.status = status
        if error:
            self.error = error
            self.status = "error"
        if attrs:
            self.attributes.update(attrs)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        return d


@dataclass
class Trace:
    name: str
    id: str = field(default_factory=lambda: _new_id("trace"))
    root_span_id: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    status: str = "ok"
    attributes: Dict[str, Any] = field(default_factory=dict)
    spans: Dict[str, Span] = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at) * 1000, 3)

    def end(self, status: str = "ok") -> None:
        self.ended_at = time.time()
        self.status = status
        # close any open spans
        for span in self.spans.values():
            if span.ended_at is None:
                span.end(status=status)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "root_span_id": self.root_span_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "spans": [s.to_dict() for s in sorted(self.spans.values(), key=lambda x: x.started_at)],
        }


class Tracer:
    """
    Process-wide tracer with optional SQLite persistence.
    Thread-local current span stack for nested instrumentation.
    """

    def __init__(self, persist_path: Optional[Path] = None, max_traces: int = 200):
        self.persist_path = Path(persist_path) if persist_path else None
        self.max_traces = max_traces
        self._traces: Dict[str, Trace] = {}
        self._lock = threading.RLock()
        self._local = threading.local()
        # Aggregate metrics
        self.metrics: Dict[str, Any] = {
            "requests": 0,
            "request_latency_ms": [],
            "retrieval_latency_ms": [],
            "llm_latency_ms": [],
            "tool_latency_ms": [],
            "planner_latency_ms": [],
            "job_latency_ms": [],
            "queue_wait_ms": [],
            "successes": 0,
            "failures": 0,
            "retries": 0,
        }
        if self.persist_path:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            self._load_recent()

    # ── context ───────────────────────────────────────────────────

    def _stack(self) -> List[str]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    @property
    def current_trace_id(self) -> Optional[str]:
        return getattr(self._local, "trace_id", None)

    @property
    def current_span_id(self) -> Optional[str]:
        stack = self._stack()
        return stack[-1] if stack else None

    def context_ids(self) -> Dict[str, Optional[str]]:
        return {"trace_id": self.current_trace_id, "span_id": self.current_span_id}

    # ── lifecycle ─────────────────────────────────────────────────

    def start_trace(self, name: str, **attrs: Any) -> Trace:
        trace = Trace(name=name, attributes=dict(attrs))
        root = Span(name=name, trace_id=trace.id, kind=attrs.get("kind", "request"))
        trace.root_span_id = root.id
        trace.spans[root.id] = root
        with self._lock:
            self._traces[trace.id] = trace
            self._trim()
        self._local.trace_id = trace.id
        self._stack().clear()
        self._stack().append(root.id)
        return trace

    def end_trace(self, trace_id: Optional[str] = None, status: str = "ok") -> Optional[Trace]:
        tid = trace_id or self.current_trace_id
        if not tid:
            return None
        with self._lock:
            trace = self._traces.get(tid)
            if not trace:
                return None
            trace.end(status=status)
            self._record_metrics(trace)
            self._save_trace(trace)
        if getattr(self._local, "trace_id", None) == tid:
            self._local.trace_id = None
            self._stack().clear()
        return trace

    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        parent_id: Optional[str] = None,
        **attrs: Any,
    ) -> Span:
        tid = self.current_trace_id
        if not tid:
            # orphan span — create implicit trace
            t = self.start_trace(name, kind=kind, **attrs)
            tid = t.id
        parent = parent_id or self.current_span_id
        span = Span(
            name=name,
            trace_id=tid,
            parent_id=parent,
            kind=kind,
            attributes=dict(attrs),
        )
        with self._lock:
            trace = self._traces.get(tid)
            if trace:
                trace.spans[span.id] = span
        self._stack().append(span.id)
        return span

    def end_span(
        self,
        span: Optional[Span] = None,
        status: str = "ok",
        error: Optional[str] = None,
        **attrs: Any,
    ) -> Optional[Span]:
        if span is None:
            sid = self.current_span_id
            if not sid or not self.current_trace_id:
                return None
            with self._lock:
                trace = self._traces.get(self.current_trace_id)
                span = trace.spans.get(sid) if trace else None
        if span is None:
            return None
        span.end(status=status, error=error, **attrs)
        stack = self._stack()
        if stack and stack[-1] == span.id:
            stack.pop()
        return span

    @contextmanager
    def span(self, name: str, *, kind: str = "internal", **attrs: Any) -> Generator[Span, None, None]:
        s = self.start_span(name, kind=kind, **attrs)
        try:
            yield s
            if s.ended_at is None:
                self.end_span(s, status="ok")
        except Exception as e:
            self.end_span(s, status="error", error=str(e))
            raise

    @contextmanager
    def request(self, name: str, **attrs: Any) -> Generator[Trace, None, None]:
        trace = self.start_trace(name, kind="request", **attrs)
        try:
            yield trace
            self.end_trace(trace.id, status=trace.status)
        except Exception:
            self.end_trace(trace.id, status="error")
            raise

    # ── queries ───────────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._traces.get(trace_id)
            if t:
                return t.to_dict()
        if self.persist_path:
            return self._load_trace(trace_id)
        return None

    def list_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            items = sorted(self._traces.values(), key=lambda t: t.started_at, reverse=True)
            out = [t.to_dict() for t in items[:limit]]
        if len(out) < limit and self.persist_path:
            # fill from DB
            for d in self._list_db(limit=limit):
                if not any(x["id"] == d["id"] for x in out):
                    out.append(d)
                if len(out) >= limit:
                    break
        return out[:limit]

    def summary_metrics(self) -> Dict[str, Any]:
        with self._lock:
            m = self.metrics

            def avg(xs: List[float]) -> float:
                return round(sum(xs) / len(xs), 2) if xs else 0.0

            def p95(xs: List[float]) -> float:
                if not xs:
                    return 0.0
                s = sorted(xs)
                return round(s[min(len(s) - 1, int(len(s) * 0.95))], 2)

            total = m["successes"] + m["failures"]
            return {
                "requests": m["requests"],
                "successes": m["successes"],
                "failures": m["failures"],
                "success_rate": round(m["successes"] / total, 3) if total else 0.0,
                "retries": m["retries"],
                "latency_ms": {
                    "request_avg": avg(m["request_latency_ms"]),
                    "request_p95": p95(m["request_latency_ms"]),
                    "planner_avg": avg(m["planner_latency_ms"]),
                    "retrieval_avg": avg(m["retrieval_latency_ms"]),
                    "llm_avg": avg(m["llm_latency_ms"]),
                    "tool_avg": avg(m["tool_latency_ms"]),
                    "job_avg": avg(m["job_latency_ms"]),
                    "queue_wait_avg": avg(m["queue_wait_ms"]),
                },
            }

    def record_retry(self) -> None:
        with self._lock:
            self.metrics["retries"] += 1

    # ── metrics from finished traces ──────────────────────────────

    def _record_metrics(self, trace: Trace) -> None:
        m = self.metrics
        m["requests"] += 1
        if trace.status == "ok":
            m["successes"] += 1
        else:
            m["failures"] += 1
        if trace.duration_ms is not None:
            m["request_latency_ms"].append(trace.duration_ms)
            m["request_latency_ms"] = m["request_latency_ms"][-500:]

        kind_map = {
            "planner": "planner_latency_ms",
            "retrieval": "retrieval_latency_ms",
            "llm": "llm_latency_ms",
            "tool": "tool_latency_ms",
            "job": "job_latency_ms",
            "vector": "retrieval_latency_ms",
        }
        for span in trace.spans.values():
            key = kind_map.get(span.kind)
            if key and span.duration_ms is not None:
                m[key].append(span.duration_ms)
                m[key] = m[key][-500:]
            if span.attributes.get("queue_wait_ms") is not None:
                m["queue_wait_ms"].append(float(span.attributes["queue_wait_ms"]))
                m["queue_wait_ms"] = m["queue_wait_ms"][-500:]

    def _trim(self) -> None:
        if len(self._traces) <= self.max_traces:
            return
        ordered = sorted(self._traces.values(), key=lambda t: t.started_at)
        for t in ordered[: len(self._traces) - self.max_traces]:
            self._traces.pop(t.id, None)

    # ── persistence ───────────────────────────────────────────────

    def _init_db(self) -> None:
        if not self.persist_path:
            return
        with sqlite3.connect(str(self.persist_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    status TEXT,
                    started_at REAL,
                    ended_at REAL,
                    duration_ms REAL,
                    payload TEXT
                )
                """
            )
            conn.commit()

    def _save_trace(self, trace: Trace) -> None:
        if not self.persist_path:
            return
        d = trace.to_dict()
        with sqlite3.connect(str(self.persist_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces (id, name, status, started_at, ended_at, duration_ms, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d["id"],
                    d["name"],
                    d["status"],
                    d["started_at"],
                    d["ended_at"],
                    d["duration_ms"],
                    json.dumps(d),
                ),
            )
            conn.commit()

    def _load_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        if not self.persist_path or not self.persist_path.exists():
            return None
        with sqlite3.connect(str(self.persist_path)) as conn:
            row = conn.execute(
                "SELECT payload FROM traces WHERE id = ?", (trace_id,)
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def _list_db(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.persist_path or not self.persist_path.exists():
            return []
        with sqlite3.connect(str(self.persist_path)) as conn:
            rows = conn.execute(
                "SELECT payload FROM traces ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _load_recent(self, limit: int = 50) -> None:
        for d in self._list_db(limit=limit):
            # lightweight: keep dict only via get_trace from DB; skip rehydrate
            pass


# ── Tracer scoping (PEAR 3.1 Gate 1) ────────────────────────────────
#
# Previously _default_tracer was a bare module global: every Orchestrator
# (one per authenticated user) called set_tracer(self.tracer) in __init__,
# silently repointing the ONE global at whichever user's session was built
# most recently — every other in-flight user's spans, and GET /v1/traces,
# would then read/write the wrong user's tracer.
#
# Fix: a contextvars.ContextVar instead of a bare global. Python gives each
# new native thread (ThreadingHTTPServer's per-request thread, JobManager's
# worker thread, WorkerManager's ThreadPoolExecutor thread) its own empty
# Context by default, and asyncio propagates context per-task — so setting
# the tracer once at each request/thread entry point keeps it correctly
# scoped without touching any of the 28 call sites that just do
# get_tracer().span(...).
import contextvars

_tracer_ctx: "contextvars.ContextVar[Optional[Tracer]]" = contextvars.ContextVar(
    "pear_current_tracer", default=None
)
# Fallback only for contexts with no active per-user tracer (bare scripts, tests).
_fallback_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    t = _tracer_ctx.get()
    if t is not None:
        return t
    global _fallback_tracer
    if _fallback_tracer is None:
        _fallback_tracer = Tracer()
    return _fallback_tracer


def set_tracer(tracer: Tracer) -> "contextvars.Token":
    """
    Activate `tracer` as the current context's (thread's / async task's)
    tracer. Returns a Token — pass it to reset_tracer() when the request/
    job/dispatch this was activated for is finished, so a reused thread
    (e.g. a ThreadPoolExecutor worker) can't leak one user's tracer into
    the next unrelated task run on the same OS thread.
    """
    return _tracer_ctx.set(tracer)


def reset_tracer(token: "contextvars.Token") -> None:
    try:
        _tracer_ctx.reset(token)
    except Exception:
        pass
