from __future__ import annotations

from core.plugins.base import Plugin


def slack_post(channel: str = "#general", text: str = "") -> dict:
    return {
        "ok": True,
        "dry_run": True,
        "channel": channel,
        "text": text,
        "message": f"[slack stub] #{channel}: {text}",
    }


class PluginImpl(Plugin):
    def load(self, api):
        api.register_tool(
            "slack_post",
            slack_post,
            description="Post a message to Slack (stub)",
            permission="slack_post",
            tags=["slack", "demo"],
        )
        api.register_command(
            "slack",
            lambda arg: slack_post(text=arg or "hello").get("message", ""),
        )
