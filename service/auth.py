"""Authentication & RBAC for PEAR service (v3.1 security hardening)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"
    API_CLIENT = "api_client"


# Defaults (overridable via AuthManager kwargs / config)
TOKEN_TTL_S = 12 * 3600          # 12h absolute lifetime
IDLE_TIMEOUT_S = 30 * 60         # 30m idle
MAX_FAILED_LOGINS = 5
LOCKOUT_BASE_S = 30              # progressive: base * 2^(n-1)
LOCKOUT_MAX_S = 15 * 60


@dataclass
class User:
    username: str
    role: Role
    password_hash: str
    token: str = ""  # legacy single token; prefer SessionStore
    created_at: float = field(default_factory=time.time)
    active: bool = True
    failed_logins: int = 0
    locked_until: float = 0.0

    def to_public(self) -> dict:
        return {
            "username": self.username,
            "role": self.role.value,
            "active": self.active,
            "created_at": self.created_at,
        }


@dataclass
class Session:
    token: str
    username: str
    role: Role
    created_at: float
    last_seen: float
    expires_at: float
    revoked: bool = False

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "username": self.username,
            "role": self.role.value,
            "created_at": self.created_at,
            "last_seen": self.last_seen,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            token=d["token"],
            username=d["username"],
            role=Role(d["role"]),
            created_at=float(d["created_at"]),
            last_seen=float(d["last_seen"]),
            expires_at=float(d["expires_at"]),
            revoked=bool(d.get("revoked")),
        )


def hash_password(password: str, salt: str = "pear-v220") -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


class AuthManager:
    def __init__(
        self,
        persist_path: Optional[Path] = None,
        *,
        token_ttl_s: float = TOKEN_TTL_S,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        max_failed_logins: int = MAX_FAILED_LOGINS,
    ):
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".pear" / "users.json"
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.persist_path.parent / "sessions_auth.json"
        self.token_ttl_s = token_ttl_s
        self.idle_timeout_s = idle_timeout_s
        self.max_failed_logins = max_failed_logins
        self.users: Dict[str, User] = {}
        self.sessions: Dict[str, Session] = {}
        self.revoked_tokens: Set[str] = set()
        self._load()
        self._load_sessions()
        if "admin" not in self.users:
            self.create_user("admin", "admin", Role.ADMIN)
        if "demo" not in self.users:
            self.create_user("demo", "demo", Role.USER)

    # ── persistence ───────────────────────────────────────────────

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            for u in data.get("users") or []:
                self.users[u["username"]] = User(
                    username=u["username"],
                    role=Role(u["role"]),
                    password_hash=u["password_hash"],
                    token=u.get("token") or "",
                    created_at=u.get("created_at", time.time()),
                    active=u.get("active", True),
                    failed_logins=int(u.get("failed_logins") or 0),
                    locked_until=float(u.get("locked_until") or 0),
                )
        except Exception:
            pass

    def _save(self) -> None:
        data = {
            "users": [
                {
                    "username": u.username,
                    "role": u.role.value,
                    "password_hash": u.password_hash,
                    "token": u.token,
                    "created_at": u.created_at,
                    "active": u.active,
                    "failed_logins": u.failed_logins,
                    "locked_until": u.locked_until,
                }
                for u in self.users.values()
            ]
        }
        self.persist_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_sessions(self) -> None:
        if not self.sessions_path.exists():
            return
        try:
            data = json.loads(self.sessions_path.read_text(encoding="utf-8"))
            for s in data.get("sessions") or []:
                sess = Session.from_dict(s)
                self.sessions[sess.token] = sess
            self.revoked_tokens = set(data.get("revoked") or [])
        except Exception:
            pass

    def _save_sessions(self) -> None:
        data = {
            "sessions": [s.to_dict() for s in self.sessions.values() if not s.revoked],
            "revoked": list(self.revoked_tokens)[-500:],
        }
        self.sessions_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── users ─────────────────────────────────────────────────────

    def create_user(self, username: str, password: str, role: Role) -> User:
        user = User(
            username=username,
            role=role,
            password_hash=hash_password(password),
            token=new_token(),
        )
        self.users[username] = user
        self._save()
        return user

    def _lockout_seconds(self, failed: int) -> float:
        if failed < self.max_failed_logins:
            return 0
        exp = failed - self.max_failed_logins + 1
        return min(LOCKOUT_MAX_S, LOCKOUT_BASE_S * (2 ** max(0, exp - 1)))

    def login(self, username: str, password: str) -> Optional[User]:
        user = self.users.get(username)
        if not user or not user.active:
            return None
        now = time.time()
        if user.locked_until and now < user.locked_until:
            return None
        if not hmac.compare_digest(user.password_hash, hash_password(password)):
            user.failed_logins += 1
            delay = self._lockout_seconds(user.failed_logins)
            if delay:
                user.locked_until = now + delay
            self._save()
            return None
        user.failed_logins = 0
        user.locked_until = 0.0
        # issue session token
        token = new_token()
        user.token = token  # keep last token for compatibility
        sess = Session(
            token=token,
            username=user.username,
            role=user.role,
            created_at=now,
            last_seen=now,
            expires_at=now + self.token_ttl_s,
        )
        self.sessions[token] = sess
        self._save()
        self._save_sessions()
        return user

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Alias for login() — some callers/tests expect this name."""
        return self.login(username, password)

    def login_status(self, username: str) -> Dict[str, Any]:
        user = self.users.get(username)
        if not user:
            return {"exists": False}
        now = time.time()
        locked = bool(user.locked_until and now < user.locked_until)
        return {
            "exists": True,
            "locked": locked,
            "locked_until": user.locked_until if locked else None,
            "failed_logins": user.failed_logins,
            "retry_after_s": max(0, int(user.locked_until - now)) if locked else 0,
        }

    def resolve_token(self, authorization: str) -> Optional[User]:
        if not authorization:
            return None
        token = authorization
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            return None
        if token in self.revoked_tokens:
            return None
        sess = self.sessions.get(token)
        now = time.time()
        if sess is not None:
            if sess.revoked:
                return None
            if now > sess.expires_at:
                sess.revoked = True
                self.revoked_tokens.add(token)
                self._save_sessions()
                return None
            if now - sess.last_seen > self.idle_timeout_s:
                sess.revoked = True
                self.revoked_tokens.add(token)
                self._save_sessions()
                return None
            sess.last_seen = now
            user = self.users.get(sess.username)
            if not user or not user.active:
                return None
            return user
        # legacy: static user.token match
        for user in self.users.values():
            if user.token and hmac.compare_digest(user.token, token) and user.active:
                # wrap into session if missing
                self.sessions[token] = Session(
                    token=token,
                    username=user.username,
                    role=user.role,
                    created_at=now,
                    last_seen=now,
                    expires_at=now + self.token_ttl_s,
                )
                self._save_sessions()
                return user
        return None

    def revoke_token(self, token: str) -> bool:
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.revoked_tokens.add(token)
        if token in self.sessions:
            self.sessions[token].revoked = True
        for u in self.users.values():
            if u.token == token:
                u.token = new_token()  # invalidate stored
        self._save()
        self._save_sessions()
        return True

    def revoke_all_for_user(self, username: str) -> int:
        n = 0
        for sess in self.sessions.values():
            if sess.username == username and not sess.revoked:
                sess.revoked = True
                self.revoked_tokens.add(sess.token)
                n += 1
        user = self.users.get(username)
        if user:
            user.token = new_token()
        self._save()
        self._save_sessions()
        return n

    def require(self, user: Optional[User], *roles: Role) -> User:
        if user is None:
            raise PermissionError("authentication required")
        if roles and user.role not in roles:
            raise PermissionError(f"role {user.role.value} not permitted")
        return user

    def authorize_resource(
        self,
        user: Optional[User],
        *,
        resource_owner: str,
        allow_admin: bool = True,
        roles: Optional[List[Role]] = None,
    ) -> User:
        """
        Server-side ownership check (IDOR/BOLA prevention).
        Admins may access any resource when allow_admin=True.
        """
        u = self.require(user, *(roles or (Role.USER, Role.ADMIN, Role.API_CLIENT)))
        if allow_admin and u.role == Role.ADMIN:
            return u
        if u.username != resource_owner:
            raise PermissionError("forbidden: resource belongs to another user")
        return u
