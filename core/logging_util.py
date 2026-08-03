"""Structured logging with correlation IDs (v2.40)."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional

_correlation_id: ContextVar[str] = ContextVar("pear_correlation_id", default="")
_context: ContextVar[Dict[str, Any]] = ContextVar("pear_log_context", default={})


def new_correlation_id() -> str:
    cid = f"corr_{uuid.uuid4().hex[:12]}"
    _correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    _correlation_id.set(cid or "")


def get_correlation_id() -> str:
    return _correlation_id.get() or ""


def bind_context(**kwargs) -> None:
    cur = dict(_context.get() or {})
    cur.update({k: v for k, v in kwargs.items() if v is not None})
    _context.set(cur)


def clear_context() -> None:
    _context.set({})
    _correlation_id.set("")


class StructuredFormatter(logging.Formatter):
    def __init__(self, json_mode: bool = False):
        super().__init__()
        self.json_mode = json_mode

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": get_correlation_id() or getattr(record, "correlation_id", ""),
        }
        ctx = _context.get() or {}
        if ctx:
            payload["context"] = ctx
        for key in ("trace_id", "goal_id", "job_id", "dispatch_id", "user"):
            val = getattr(record, key, None)
            if val:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if self.json_mode:
            return json.dumps(payload, default=str)
        extra = []
        if payload.get("correlation_id"):
            extra.append(f"cid={payload['correlation_id']}")
        if "context" in payload:
            extra.append(str(payload["context"]))
        suffix = (" " + " ".join(extra)) if extra else ""
        return f"{payload['ts']} {payload['level']} [{payload['logger']}] {payload['msg']}{suffix}"


def setup_logging(level: str = "INFO", json_mode: bool = False) -> logging.Logger:
    root = logging.getLogger("pear")
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter(json_mode=json_mode))
    root.addHandler(handler)
    root.propagate = False
    return root


def get_logger(name: str = "pear") -> logging.Logger:
    return logging.getLogger(name)
