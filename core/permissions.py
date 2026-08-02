"""
Permission system for PEAR agents and desktop tools.
"""

from __future__ import annotations

from typing import Dict, Optional, Set

from .desktop import PERM_GROUPS, ALL_DESKTOP_PERMS

MEDIA_PERM_GROUPS = {
    "microphone": {"transcribe", "listen"},
    "camera": {"capture_image"},
    "screen_capture": {"take_screenshot", "ingest_screenshot"},
    "image_processing": {"ocr", "describe_image", "process_media"},
}


class Permissions:
    """
    Action allow-list with optional groups.
    Policies: always / once / confirm / never (metadata for UI).
    """

    def __init__(self, allowed: Optional[Set[str]] = None):
        self.allowed: Set[str] = set(allowed or set())
        # Default safe grants for chat agents
        self.allowed |= {
            "chat",
            "read_document",
            "summarize_text",
            "search_files",
            "list_directory",
            "get_system_info",
            "ocr",
            "describe_image",
            "process_media",
            "transcribe",
        }
        # policy hints for sensitive actions
        self.policies: Dict[str, str] = {
            "open_application": "confirm",
            "delete_file": "confirm",
            "move_file": "confirm",
            "copy_file": "once",
            "take_screenshot": "confirm",
            "open_folder": "always",
            "list_directory": "always",
            "search_files": "always",
            "get_system_info": "always",
            "create_folder": "once",
            "rename_file": "once",
        }

    def can(self, action: str) -> bool:
        return action in self.allowed

    def grant(self, action: str) -> None:
        self.allowed.add(action)

    def grant_group(self, group: str) -> None:
        actions = PERM_GROUPS.get(group) or MEDIA_PERM_GROUPS.get(group) or set()
        for action in actions:
            self.allowed.add(action)

    def revoke(self, action: str) -> None:
        self.allowed.discard(action)

    def revoke_group(self, group: str) -> None:
        actions = PERM_GROUPS.get(group) or MEDIA_PERM_GROUPS.get(group) or set()
        for action in actions:
            self.allowed.discard(action)

    def require(self, action: str) -> None:
        if not self.can(action):
            raise PermissionError(f"Action '{action}' is not permitted for this agent.")

    def policy(self, action: str) -> str:
        return self.policies.get(action, "confirm")

    def set_policy(self, action: str, policy: str) -> None:
        if policy not in ("always", "once", "confirm", "never"):
            raise ValueError(f"Invalid policy: {policy}")
        self.policies[action] = policy
        if policy == "never":
            self.allowed.discard(action)
        elif policy == "always":
            self.allowed.add(action)

    def summary(self) -> Dict[str, object]:
        return {
            "allowed": sorted(self.allowed),
            "policies": dict(self.policies),
            "groups": {k: sorted(v) for k, v in PERM_GROUPS.items()},
        }
