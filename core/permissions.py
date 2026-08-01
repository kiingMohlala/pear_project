"""
Simple permission / capability gates.
"""

from __future__ import annotations

from typing import Set


class Permissions:
    """
    Controls which high-risk actions an agent may perform.
    In later versions this will be more granular and user-configurable.
    """

    def __init__(self, allowed: Set[str] | None = None):
        # Default safe set for v0.1 / v0.2
        self.allowed: Set[str] = allowed or {
            "chat",
            "read_file",
            "summarize",
            "notes",
            "open_app",
            "open_folder",
            "search_files",
        }

    def can(self, action: str) -> bool:
        return action in self.allowed

    def grant(self, action: str) -> None:
        self.allowed.add(action)

    def revoke(self, action: str) -> None:
        self.allowed.discard(action)

    def require(self, action: str) -> None:
        if not self.can(action):
            raise PermissionError(f"Action '{action}' is not permitted for this agent.")
