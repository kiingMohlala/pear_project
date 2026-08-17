"""Per-user session isolation (memory, goals, jobs, workflows, permissions)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


@dataclass
class UserSession:
    user_id: str
    orchestrator: Any
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """
    One Orchestrator per user with isolated persist_dir. Thread-safe
    registry.

    PEAR 3.1 Gate 6: sessions used to accumulate forever with no way to
    release them — every distinct user who ever logged in kept their own
    Orchestrator (with its own JobManager worker threads, WorkerManager
    thread pool, etc.) alive in memory for the lifetime of the process.
    evict()/evict_idle() below let a caller reclaim genuinely idle
    sessions, without ever interrupting one that has active work, and
    with the resources that session held actually released (not just the
    dict entry removed). A later get() for the same user reconstructs
    cleanly from the same on-disk persist_dir — nothing here deletes
    persisted state, only the in-memory Orchestrator and its threads.
    """

    def __init__(
        self,
        data_root: Optional[Path] = None,
        llm: Any = None,
        idle_timeout_s: float = 3600.0,
    ):
        self.data_root = Path(data_root) if data_root else Path.home() / ".pear" / "sessions"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.idle_timeout_s = idle_timeout_s
        self._sessions: Dict[str, UserSession] = {}
        self._lock = threading.RLock()
        self._sweeper_thread: Optional[threading.Thread] = None
        self._sweeper_stop = threading.Event()

    def _build_orchestrator(self, user_id: str) -> Any:
        from core.memory import Memory
        from core.orchestrator import Orchestrator
        from core.llm import create_llm, EchoLLM
        from agents import (
            PersonalAgent, DesktopAgent, FinanceAgent, LegalAgent,
            BrowserAgent, ResearchAgent, ComputerUseAgent, EmailAgent,
            CalendarAgent, ReviewerAgent, CriticAgent,
        )

        persist = self.data_root / user_id
        persist.mkdir(parents=True, exist_ok=True)
        llm = self.llm or create_llm()
        mem = Memory(session_id=user_id, persist_dir=persist)
        orch = Orchestrator(memory=mem, llm=llm, user_id=user_id)
        # register standard agents
        orch.register(PersonalAgent(llm=llm), default=True)
        for cls in (
            DesktopAgent, FinanceAgent, LegalAgent, BrowserAgent,
            ResearchAgent, ComputerUseAgent, EmailAgent, CalendarAgent,
            ReviewerAgent, CriticAgent,
        ):
            try:
                if cls is BrowserAgent:
                    # PEAR 3.1 Gate 10: explicit injection of this
                    # orchestrator's own BrowserManager — the actual fix.
                    # Without this, BrowserAgent would fall back to
                    # constructing its own private manager, which is safe
                    # but pointless: orch.browser_manager would sit there
                    # unused while every action actually ran through a
                    # different, second manager instance.
                    orch.register(cls(browser_manager=orch.browser_manager))
                elif cls in (DesktopAgent, ComputerUseAgent):
                    orch.register(cls())
                else:
                    orch.register(cls(llm=llm))
            except Exception:
                try:
                    orch.register(cls())
                except Exception:
                    pass
        return orch

    def get(self, user_id: str) -> UserSession:
        # Gate 6: the check-then-create is entirely inside the lock, so
        # two concurrent requests for a brand-new user_id can't each build
        # their own Orchestrator and race to store it — the second caller
        # to acquire the lock sees the first's session already there.
        with self._lock:
            if user_id not in self._sessions:
                orch = self._build_orchestrator(user_id)
                self._sessions[user_id] = UserSession(user_id=user_id, orchestrator=orch)
            sess = self._sessions[user_id]
            sess.last_access = time.time()
            return sess

    def _is_busy(self, orch: Any) -> bool:
        """True if this orchestrator has active work an eviction must not
        interrupt: a running job, a running goal, or an in-flight worker
        dispatch."""
        try:
            from core.job import JobStatus
            if any(j.status == JobStatus.RUNNING for j in orch.jobs.list_jobs(limit=1000)):
                return True
        except Exception:
            pass
        try:
            from core.goals import GoalStatus
            if any(g.status == GoalStatus.RUNNING for g in orch.goals.list_goals()):
                return True
        except Exception:
            pass
        try:
            from core.workers import DispatchStatus
            active = {DispatchStatus.DISPATCHED, DispatchStatus.ACKED, DispatchStatus.RUNNING}
            if any(d.status in active for d in orch.workers.dispatches.values()):
                return True
        except Exception:
            pass
        return False

    def _shutdown_orchestrator(self, orch: Any) -> None:
        """Release resources cleanly on eviction. Memory/CredentialStore
        are plain files with no held-open connection to close; Tracer
        opens a fresh sqlite3 connection per write rather than holding
        one, so nothing to close there either. What DOES need explicit
        shutdown: JobManager's persistent worker threads,
        WorkerManager's ThreadPoolExecutor, and (PEAR 3.1 Gate 10) this
        user's BrowserManager — closing its Playwright browser/context/
        page so no orphan browser process survives the session."""
        try:
            orch.jobs.stop(timeout=2.0)
        except Exception:
            pass
        try:
            orch.workers.shutdown()
        except Exception:
            pass
        try:
            orch.browser_manager.close()
        except Exception:
            pass

    def evict(self, user_id: str, *, force: bool = False) -> bool:
        """
        Evict a single session. Returns True if it was evicted, False if
        it was kept (has active work and force=False) or didn't exist.
        Never destroys persisted job/goal/workflow state — only the
        in-memory Orchestrator and the threads it owns.
        """
        with self._lock:
            sess = self._sessions.get(user_id)
            if sess is None:
                return False
            if not force and self._is_busy(sess.orchestrator):
                return False
            orch = sess.orchestrator
            del self._sessions[user_id]
        # Shut down outside the lock — stop()/shutdown() can block briefly
        # joining threads, and holding the registry lock during that would
        # block unrelated users' get() calls for no reason.
        self._shutdown_orchestrator(orch)
        return True

    def evict_idle(self, max_idle_s: Optional[float] = None) -> List[str]:
        """Sweep every session and evict the ones idle longer than
        max_idle_s (defaults to self.idle_timeout_s) that aren't
        currently busy. Returns the user_ids actually evicted."""
        threshold = self.idle_timeout_s if max_idle_s is None else max_idle_s
        now = time.time()
        with self._lock:
            candidates = [
                uid for uid, sess in self._sessions.items()
                if now - sess.last_access > threshold
            ]
        evicted = []
        for uid in candidates:
            if self.evict(uid):
                evicted.append(uid)
        return evicted

    def start_idle_sweeper(self, interval_s: float = 60.0) -> None:
        """Optional: run evict_idle() on a background timer. Not started
        automatically — a deployment decides its own sweep cadence."""
        if self._sweeper_thread is not None:
            return
        self._sweeper_stop.clear()

        def _loop():
            while not self._sweeper_stop.wait(interval_s):
                try:
                    self.evict_idle()
                except Exception:
                    pass

        self._sweeper_thread = threading.Thread(target=_loop, name="pear-session-sweeper", daemon=True)
        self._sweeper_thread.start()

    def stop_idle_sweeper(self) -> None:
        if self._sweeper_thread is None:
            return
        self._sweeper_stop.set()
        self._sweeper_thread.join(timeout=2.0)
        self._sweeper_thread = None

    def drop(self, user_id: str) -> None:
        """Forcibly evict regardless of activity — prefer evict() unless
        you specifically want to interrupt active work."""
        self.evict(user_id, force=True)

    def list_sessions(self) -> list:
        with self._lock:
            return [
                {
                    "user_id": s.user_id,
                    "created_at": s.created_at,
                    "last_access": s.last_access,
                    "busy": self._is_busy(s.orchestrator),
                }
                for s in self._sessions.values()
            ]
