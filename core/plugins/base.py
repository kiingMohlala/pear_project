"""Plugin base class with lifecycle hooks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import PluginAPI
    from .manifest import PluginManifest


class Plugin(ABC):
    """
    Lifecycle: install → load → enable → disable → uninstall
    """

    manifest: "PluginManifest"

    def install(self, api: "PluginAPI") -> None:
        """One-time setup (optional)."""

    @abstractmethod
    def load(self, api: "PluginAPI") -> None:
        """Register tools/agents/commands with the API."""

    def enable(self, api: "PluginAPI") -> None:
        """Called when plugin is enabled at runtime."""

    def disable(self, api: "PluginAPI") -> None:
        """Called when plugin is disabled — should stop background work."""

    def uninstall(self, api: "PluginAPI") -> None:
        """Cleanup on removal."""
