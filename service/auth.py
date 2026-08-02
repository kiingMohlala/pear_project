"""Authentication & RBAC for PEAR service (v2.20)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"
    API_CLIENT = "api_client"


@dataclass
class User:
    username: str
    role: Role
    password_hash: str
    token: str
    created_at: float = field(default_factory=time.time)
    active: bool = True

    def to_public(self) -> dict:
        return {
            "username": self.username,
            "role": self.role.value,
            "active": self.active,
            "created_at": self.created_at,
        }


def hash_password(password: str, salt: str = "pear-v220") -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


class AuthManager:
    def __init__(self, persist_path: Optional[Path] = None):
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".pear" / "users.json"
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        self.users: Dict[str, User] = {}
        self._load()
        if "admin" not in self.users:
            self.create_user("admin", "admin", Role.ADMIN)
        if "demo" not in self.users:
            self.create_user("demo", "demo", Role.USER)

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
                    token=u["token"],
                    created_at=u.get("created_at", time.time()),
                    active=u.get("active", True),
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
                }
                for u in self.users.values()
            ]
        }
        self.persist_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def create_user(self, username: str, password: str, role: Role = Role.USER) -> User:
        token = secrets.token_urlsafe(24)
        user = User(
            username=username,
            role=role,
            password_hash=hash_password(password),
            token=token,
        )
        self.users[username] = user
        self._save()
        return user

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.users.get(username)
        if not user or not user.active:
            return None
        if hmac.compare_digest(user.password_hash, hash_password(password)):
            return user
        return None

    def resolve_token(self, token: str) -> Optional[User]:
        if not token:
            return None
        # Bearer prefix
        token = token.replace("Bearer ", "").strip()
        for user in self.users.values():
            if user.active and hmac.compare_digest(user.token, token):
                return user
        return None

    def require(self, user: Optional[User], *roles: Role) -> User:
        if user is None:
            raise PermissionError("authentication required")
        if roles and user.role not in roles and user.role != Role.ADMIN:
            raise PermissionError(f"role {user.role.value} not permitted")
        return user
