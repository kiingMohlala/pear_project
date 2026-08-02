"""Per-user session isolation (memory, goals, jobs, workflows, permissions)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

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
    One Orchestrator per user with isolated persist_dir.
    Thread-safe registry.
    """

    def __init__(self, data_root: Optional[Path] = None, llm: Any = None):
        self.data_root = Path(data_root) if data_root else Path.home() / ".pear" / "sessions"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self._sessions: Dict[str, UserSession] = {}
        self._lock = threading.RLock()

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
        orch = Orchestrator(memory=mem, llm=llm)
        # register standard agents
        orch.register(PersonalAgent(llm=llm), default=True)
        for cls in (
            DesktopAgent, FinanceAgent, LegalAgent, BrowserAgent,
            ResearchAgent, ComputerUseAgent, EmailAgent, CalendarAgent,
            ReviewerAgent, CriticAgent,
        ):
            try:
                if cls in (DesktopAgent, BrowserAgent, ComputerUseAgent):
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
        with self._lock:
            if user_id not in self._sessions:
                orch = self._build_orchestrator(user_id)
                self._sessions[user_id] = UserSession(user_id=user_id, orchestrator=orch)
            sess = self._sessions[user_id]
            sess.last_access = time.time()
            return sess

    def drop(self, user_id: str) -> None:
        with self._lock:
            self._sessions.pop(user_id, None)

    def list_sessions(self) -> list:
        with self._lock:
            return [
                {
                    "user_id": s.user_id,
                    "created_at": s.created_at,
                    "last_access": s.last_access,
                }
                for s in self._sessions.values()
            ]
