"""Operations: resource metrics, diagnostics, integrity (v2.40)."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


def resource_usage() -> Dict[str, Any]:
    out: Dict[str, Any] = {"pid": os.getpid()}
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        out["user_cpu_s"] = ru.ru_utime
        out["max_rss_kb"] = ru.ru_maxrss
    except Exception:
        pass
    try:
        import psutil  # optional
        p = psutil.Process()
        out["cpu_percent"] = p.cpu_percent(interval=0.0)
        out["memory_mb"] = round(p.memory_info().rss / 1e6, 2)
    except Exception:
        pass
    return out


def diagnostics(orch: Optional["Orchestrator"] = None) -> Dict[str, Any]:
    from .config import get_config
    cfg = get_config()
    data: Dict[str, Any] = {
        "ts": time.time(),
        "config_profile": cfg.get("profile"),
        "resource": resource_usage(),
    }
    if orch is None:
        return data
    try:
        data["agents"] = list(getattr(orch, "agents", {}).keys())
    except Exception:
        data["agents"] = []
    try:
        data["workers"] = orch.workers.metrics_snapshot()
        data["worker_list"] = orch.workers.list_workers()
    except Exception as e:
        data["workers_error"] = str(e)
    try:
        data["goals"] = len(orch.goals.list_goals())
    except Exception:
        pass
    try:
        data["learning"] = orch.learning.status()
    except Exception:
        pass
    return data


def integrity_check(data_dir) -> Dict[str, Any]:
    from pathlib import Path
    root = Path(data_dir)
    issues = []
    if not root.exists():
        issues.append(f"data_dir missing: {root}")
    else:
        for sub in ("goals", "learning"):
            p = root / sub
            if p.exists() and not p.is_dir():
                issues.append(f"{sub} is not a directory")
    return {"ok": len(issues) == 0, "issues": issues, "data_dir": str(root)}
