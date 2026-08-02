"""
Browser Agent (v0.80) – secure web navigation & extraction.

Playwright-backed when available; simulated navigation otherwise.
Downloads go to workspace/downloads. Sensitive actions need approval.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import Agent
from core.task import Task
from core.browser import (
    BrowserManager,
    get_browser_manager,
    BROWSER_PERM_GROUPS,
    playwright_available,
)


class BrowserAgent(Agent):
    def __init__(self, download_dir: Optional[Path] = None, **kwargs):
        super().__init__(
            name="browser",
            description=(
                "Browses the web: open URLs, search, extract text/tables, "
                "download files into the workspace, click/type with approval, "
                "and save pages or screenshots."
            ),
            capabilities=[
                "browser",
                "web_search",
                "navigation",
                "web_extraction",
                "web_automation",
            ],
            allowed_tools=[
                "open_url",
                "search_web",
                "extract_text",
                "extract_tables",
                "download_file",
                "take_webpage_screenshot",
                "save_page",
            ],
            system_prompt="You are PEAR's Browser Agent. Prefer read-only browsing; ask approval for forms/logins/uploads.",
            **kwargs,
        )
        # downloads under workspace
        if download_dir is None:
            download_dir = Path.home() / "PEAR_Workspace" / "downloads"
        self.browser = get_browser_manager(download_dir=download_dir)
        self.permissions.grant("chat")
        for group, actions in BROWSER_PERM_GROUPS.items():
            if group in ("browser_read",):
                for a in actions:
                    self.permissions.grant(a)
            elif group in ("browser_write", "browser_download"):
                for a in actions:
                    self.permissions.grant(a)
                    self.permissions.set_policy(a, "confirm")
            else:
                for a in actions:
                    self.permissions.grant(a)
                    self.permissions.set_policy(a, "confirm")
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "open url", "browse", "website", "http://", "https://",
            "search web", "google", "duckduckgo", "scrape", "extract text",
            "download from", "webpage", "browser", "click ", "fill form",
        ]
        hits = sum(1 for s in signals if s in obj)
        if hits:
            score = max(score, min(0.95, 0.55 + 0.1 * hits))
        # URL presence
        if re.search(r"https?://|\w+\.\w{2,}", obj):
            score = max(score, 0.7)
        return score

    def _span(self, name: str, **attrs):
        try:
            from core.tracing import get_tracer
            return get_tracer().span(name, kind="tool", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _browser_emit(self, kind: str, **payload):
        try:
            from core.events import EventType
            self.events.emit(EventType.NOTE, {"kind": kind, **payload}, source="browser")
        except Exception:
            pass

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        text = task.objective
        lower = text.lower().strip()

        m = re.match(r"approve\s+browser\s+(\w+)", lower)
        if m:
            return self._approve(m.group(1))

        if lower in ("browser", "browser help", "/browser"):
            return self._help()

        if lower in ("browser history", "/history", "history"):
            return self._history()

        if lower in ("downloads", "/downloads", "list downloads"):
            return self._downloads()

        # open url
        m = re.search(r"(?:open\s+url|open|browse|go\s+to|navigate\s+to)\s+(\S+)", lower)
        if m and ("http" in m.group(1) or "." in m.group(1)):
            return self._open(m.group(1).strip().rstrip(".,)"))

        m = re.search(r"(https?://\S+)", text)
        if m and any(k in lower for k in ("open", "browse", "go to", "visit", "navigate")):
            return self._open(m.group(1).rstrip(".,)"))

        # search
        m = re.search(r"(?:search\s+web|web\s+search|search\s+for)\s+(.+)", lower)
        if m:
            return self._search(m.group(1).strip().rstrip("."))

        if lower in ("back", "go back"):
            return self._nav("back")
        if lower in ("forward", "go forward"):
            return self._nav("forward")
        if lower in ("refresh", "reload"):
            return self._nav("refresh")

        # extract
        if "extract table" in lower or "extract tables" in lower:
            return self._extract_tables()
        m = re.search(r"extract\s+text(?:\s+from\s+(\S+))?", lower)
        if m or "extract text" in lower:
            sel = m.group(1) if m and m.group(1) else "body"
            return self._extract_text(sel)

        # download
        m = re.search(r"download\s+(?:file\s+)?(?:from\s+)?(\S+)", lower)
        if m and ("download" in lower):
            target = m.group(1).strip()
            if target.startswith("http"):
                return self._download(url=target)
            return self._download(selector=target)

        # type / click – approval
        m = re.search(r"click\s+(.+)", lower)
        if m:
            return self._request_write("click", selector=m.group(1).strip().strip("'\""))

        m = re.search(r"(?:type|fill)\s+(.+?)\s+(?:into|in)\s+(.+)", lower)
        if m:
            return self._request_write(
                "type_text",
                text=m.group(1).strip().strip("'\""),
                selector=m.group(2).strip().strip("'\""),
            )

        if "screenshot" in lower and ("page" in lower or "web" in lower or "browser" in lower):
            return self._screenshot()

        if "save page" in lower or "save webpage" in lower:
            return self._save_page()

        return self._help()

    def _help(self) -> Dict[str, Any]:
        pw = "available" if playwright_available() else "not installed (simulated mode)"
        return {
            "ok": True,
            "reply": (
                "Browser commands:\n"
                "• open url <https://…> · search web <query>\n"
                "• back / forward / refresh\n"
                "• extract text [selector] · extract tables\n"
                "• download <url|selector>\n"
                "• click <selector> · type <text> into <selector> (approval)\n"
                "• save page · screenshot page\n"
                "• history · downloads\n"
                f"Playwright: {pw}\n"
                f"Downloads: {self.browser.download_dir}"
            ),
            "action": "help",
            "playwright": playwright_available(),
        }

    def _open(self, url: str) -> Dict[str, Any]:
        with self._span("browser.open_url", url=url):
            self.permissions.require("open_url")
            result = self.browser.open_url(url)
        self._browser_emit("browser_navigated", url=result.get("url") or url)
        # index page text into knowledge when available
        if result.get("ok") and not result.get("simulated"):
            ext = self.browser.extract_text("body")
            if ext.get("ok") and ext.get("text"):
                try:
                    self.memory.knowledge.add_document(
                        name=f"web:{result.get('title') or url}"[:80],
                        text=ext["text"][:20000],
                        source_path=result.get("url"),
                        metadata={"type": "webpage", "url": result.get("url")},
                    )
                except Exception:
                    pass
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error") or str(result),
            "action": "open_url",
            "data": result,
        }

    def _search(self, query: str) -> Dict[str, Any]:
        with self._span("browser.search_web", query=query):
            self.permissions.require("search_web")
            result = self.browser.search_web(query)
        self._browser_emit("browser_search", query=query)
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error") or f"Searched: {query}",
            "action": "search_web",
            "data": result,
        }

    def _nav(self, action: str) -> Dict[str, Any]:
        with self._span(f"browser.{action}"):
            self.permissions.require(action if action != "refresh" else "refresh")
            fn = getattr(self.browser, action)
            result = fn()
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error") or action,
            "action": action,
            "data": result,
        }

    def _extract_text(self, selector: str) -> Dict[str, Any]:
        with self._span("browser.extract_text", selector=selector):
            self.permissions.require("extract_text")
            result = self.browser.extract_text(selector)
        text = (result.get("text") or "")[:3000]
        if result.get("ok"):
            reply = f"**{result.get('title') or result.get('url')}**\n\n{text}"
            if len(result.get("text") or "") > 3000:
                reply += "\n…"
        else:
            reply = result.get("error") or "Extract failed"
        return {"ok": result.get("ok", False), "reply": reply, "action": "extract_text", "data": result}

    def _extract_tables(self) -> Dict[str, Any]:
        with self._span("browser.extract_tables"):
            self.permissions.require("extract_tables")
            result = self.browser.extract_tables()
        if not result.get("ok"):
            return {"ok": False, "reply": result.get("error"), "action": "extract_tables"}
        tables = result.get("tables") or []
        if not tables:
            return {"ok": True, "reply": "No tables found on the page.", "action": "extract_tables", "data": result}
        lines = [f"Found {len(tables)} table(s):\n"]
        for i, table in enumerate(tables[:3]):
            lines.append(f"### Table {i+1}")
            for row in table[:8]:
                lines.append(" | ".join(str(c) for c in row))
            lines.append("")
        return {"ok": True, "reply": "\n".join(lines), "action": "extract_tables", "data": result}

    def _download(self, url: Optional[str] = None, selector: Optional[str] = None) -> Dict[str, Any]:
        # approval for downloads
        approval_id = f"dl_{abs(hash(url or selector or '')) % 10_000_000}"
        self.pending_approvals[approval_id] = {"action": "download_file", "url": url, "selector": selector}
        return {
            "ok": True,
            "reply": (
                f"Download requires approval.\n"
                f"Target: {url or selector}\n"
                f"Say: approve browser {approval_id}"
            ),
            "action": "needs_approval",
            "approval_id": approval_id,
        }

    def _request_write(self, action: str, **params) -> Dict[str, Any]:
        approval_id = f"bw_{abs(hash(str(params))) % 10_000_000}"
        self.pending_approvals[approval_id] = {"action": action, **params}
        return {
            "ok": True,
            "reply": (
                f"Browser write action `{action}` needs approval.\n"
                f"Params: {params}\n"
                f"Say: approve browser {approval_id}"
            ),
            "action": "needs_approval",
            "approval_id": approval_id,
        }

    def _approve(self, approval_id: str) -> Dict[str, Any]:
        pending = self.pending_approvals.pop(approval_id, None)
        if not pending:
            return {"ok": False, "reply": f"Unknown approval: {approval_id}", "action": "approve"}
        action = pending["action"]
        with self._span(f"browser.{action}.approved"):
            if action == "download_file":
                self.permissions.require("download_file")
                result = self.browser.download_file(url=pending.get("url"), selector=pending.get("selector"))
            elif action == "click":
                self.permissions.require("click")
                result = self.browser.click(pending["selector"])
            elif action == "type_text":
                self.permissions.require("type_text")
                result = self.browser.type_text(pending["selector"], pending.get("text", ""))
            else:
                return {"ok": False, "reply": f"Unsupported action {action}", "action": "approve"}
        self._browser_emit("browser_action", action=action, ok=result.get("ok"))
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error") or str(result),
            "action": action,
            "data": result,
        }

    def _screenshot(self) -> Dict[str, Any]:
        with self._span("browser.screenshot"):
            self.permissions.require("take_webpage_screenshot")
            result = self.browser.take_webpage_screenshot()
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error"),
            "action": "screenshot",
            "data": result,
        }

    def _save_page(self) -> Dict[str, Any]:
        with self._span("browser.save_page"):
            self.permissions.require("save_page")
            result = self.browser.save_page()
        if result.get("ok"):
            # index into knowledge
            try:
                text = Path(result["path"]).read_text(encoding="utf-8", errors="replace")[:15000]
                self.memory.knowledge.add_document(
                    name=f"saved:{self.browser.session.title or 'page'}",
                    text=text,
                    source_path=result["path"],
                    metadata={"type": "webpage_save", "url": self.browser.session.current_url},
                )
            except Exception:
                pass
        return {
            "ok": result.get("ok", False),
            "reply": result.get("message") or result.get("error"),
            "action": "save_page",
            "data": result,
        }

    def _history(self) -> Dict[str, Any]:
        h = self.browser.history_list()
        lines = [f"{'>' if i == h['index'] else ' '} {u}" for i, u in enumerate(h.get("history") or [])]
        return {
            "ok": True,
            "reply": "## Browser history\n" + ("\n".join(lines) if lines else "(empty)"),
            "action": "history",
            "data": h,
        }

    def _downloads(self) -> Dict[str, Any]:
        files = list(self.browser.download_dir.glob("*")) if self.browser.download_dir.exists() else []
        session_dl = self.browser.session.downloads
        lines = [f"Download dir: {self.browser.download_dir}", ""]
        for d in session_dl[-10:]:
            lines.append(f"• {d.get('filename')} → {d.get('path')}")
        for f in files[:10]:
            if not any(d.get("path") == str(f) for d in session_dl):
                lines.append(f"• {f.name}")
        return {"ok": True, "reply": "\n".join(lines), "action": "downloads"}
