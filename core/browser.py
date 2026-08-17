"""
Browser automation helpers (v0.80).

Uses Playwright when installed; otherwise tools return structured errors
so the agent and planner still function offline.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote_plus, urlparse

# Permission groups
BROWSER_PERM_GROUPS: Dict[str, Set[str]] = {
    "browser_read": {
        "open_url",
        "search_web",
        "back",
        "forward",
        "refresh",
        "extract_text",
        "extract_tables",
        "take_webpage_screenshot",
        "save_page",
        "browser_history",
    },
    "browser_write": {
        "click",
        "type_text",
        "select_option",
    },
    "browser_download": {
        "download_file",
    },
    "browser_upload": {
        "upload_file",
    },
    "browser_login": {
        "browser_login",
        "submit_form",
    },
}


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


@dataclass
class BrowserSession:
    """In-memory browser session state (works with or without Playwright)."""

    id: str = field(default_factory=lambda: f"brow_{uuid.uuid4().hex[:10]}")
    current_url: str = "about:blank"
    history: List[str] = field(default_factory=list)
    history_index: int = -1
    title: str = ""
    last_text: str = ""
    downloads: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    # Playwright handles (optional)
    _page: Any = field(default=None, repr=False)
    _browser: Any = field(default=None, repr=False)
    _playwright: Any = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "current_url": self.current_url,
            "title": self.title,
            "history": list(self.history),
            "history_index": self.history_index,
            "downloads": list(self.downloads),
            "created_at": self.created_at,
        }

    def push_url(self, url: str) -> None:
        # truncate forward history
        if self.history_index < len(self.history) - 1:
            self.history = self.history[: self.history_index + 1]
        self.history.append(url)
        self.history_index = len(self.history) - 1
        self.current_url = url


class BrowserManager:
    """
    Owns exactly one browser session (Playwright browser/context/page when
    available, or simulated navigation state otherwise). Does NOT manage
    its own lifetime as a shared/global resource — PEAR 3.1 Gate 10:
    ownership belongs to whichever Orchestrator constructs it (one per
    authenticated user via SessionManager), or to whoever else
    instantiates it directly (e.g. the CLI, which gets its own private
    instance). Never share one BrowserManager across two Orchestrators.
    Downloads land under whatever download_dir the owner passes in.
    """

    def __init__(self, download_dir: Optional[Path] = None, headless: bool = True):
        self.download_dir = Path(download_dir) if download_dir else Path.home() / "PEAR_Workspace" / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.session = BrowserSession()
        self._started = False

    def ensure_browser(self) -> Dict[str, Any]:
        if not playwright_available():
            return {"ok": False, "error": "Playwright not installed. pip install playwright && playwright install chromium"}
        if self._started and self.session._page is not None:
            return {"ok": True}
        try:
            from playwright.sync_api import sync_playwright
            self.session._playwright = sync_playwright().start()
            self.session._browser = self.session._playwright.chromium.launch(headless=self.headless)
            context = self.session._browser.new_context(accept_downloads=True)
            self.session._page = context.new_page()
            self._started = True
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"Failed to start browser: {e}"}

    def close(self) -> None:
        try:
            if self.session._browser:
                self.session._browser.close()
            if self.session._playwright:
                self.session._playwright.stop()
        except Exception:
            pass
        self._started = False
        self.session._page = None
        self.session._browser = None
        self.session._playwright = None

    # ── navigation ────────────────────────────────────────────────

    def open_url(self, url: str) -> Dict[str, Any]:
        url = url.strip()
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        started = self.ensure_browser()
        if not started.get("ok"):
            # offline simulation: record navigation only
            self.session.push_url(url)
            self.session.title = urlparse(url).netloc or url
            return {
                "ok": True,
                "simulated": True,
                "url": url,
                "title": self.session.title,
                "message": f"Navigated (simulated — Playwright unavailable): {url}",
                "warning": started.get("error"),
            }
        page = self.session._page
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self.session.push_url(page.url)
            self.session.title = page.title()
            self.session.last_text = ""
            return {
                "ok": True,
                "url": page.url,
                "title": self.session.title,
                "message": f"Opened {page.url}",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def search_web(self, query: str) -> Dict[str, Any]:
        q = quote_plus(query)
        return self.open_url(f"https://duckduckgo.com/?q={q}")

    def back(self) -> Dict[str, Any]:
        if self.session.history_index <= 0:
            return {"ok": False, "error": "No previous page"}
        self.session.history_index -= 1
        url = self.session.history[self.session.history_index]
        return self.open_url(url)

    def forward(self) -> Dict[str, Any]:
        if self.session.history_index >= len(self.session.history) - 1:
            return {"ok": False, "error": "No forward page"}
        self.session.history_index += 1
        url = self.session.history[self.session.history_index]
        return self.open_url(url)

    def refresh(self) -> Dict[str, Any]:
        if self.session.current_url in ("", "about:blank"):
            return {"ok": False, "error": "No page loaded"}
        return self.open_url(self.session.current_url)

    # ── interaction ───────────────────────────────────────────────

    def click(self, selector: str) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {"ok": False, "error": started.get("error") or "Browser not available", "needs_playwright": True}
        try:
            self.session._page.click(selector, timeout=10000)
            self.session.current_url = self.session._page.url
            return {"ok": True, "message": f"Clicked {selector}", "url": self.session.current_url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def type_text(self, selector: str, text: str) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {"ok": False, "error": started.get("error") or "Browser not available", "needs_playwright": True}
        try:
            self.session._page.fill(selector, text, timeout=10000)
            return {"ok": True, "message": f"Typed into {selector}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def select_option(self, selector: str, value: str) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {"ok": False, "error": started.get("error") or "Browser not available", "needs_playwright": True}
        try:
            self.session._page.select_option(selector, value, timeout=10000)
            return {"ok": True, "message": f"Selected {value} on {selector}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── extraction ────────────────────────────────────────────────

    def extract_text(self, selector: str = "body") -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            # simulated: empty extraction with note
            return {
                "ok": True,
                "simulated": True,
                "text": "",
                "url": self.session.current_url,
                "message": "No live page (Playwright unavailable)",
                "warning": started.get("error"),
            }
        try:
            loc = self.session._page.locator(selector).first
            text = loc.inner_text(timeout=10000)
            self.session.last_text = text
            return {
                "ok": True,
                "text": text[:50000],
                "length": len(text),
                "url": self.session.current_url,
                "title": self.session.title,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def extract_tables(self) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {
                "ok": True,
                "simulated": True,
                "tables": [],
                "message": "No live page (Playwright unavailable)",
            }
        try:
            tables = self.session._page.eval_on_selector_all(
                "table",
                """els => els.map(t => {
                    const rows = [...t.querySelectorAll('tr')];
                    return rows.map(r => [...r.querySelectorAll('th,td')].map(c => c.innerText.trim()));
                })""",
            )
            return {"ok": True, "tables": tables[:20], "count": len(tables)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── download / upload / save ──────────────────────────────────

    def download_file(self, url: Optional[str] = None, selector: Optional[str] = None) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {"ok": False, "error": started.get("error") or "Browser not available", "needs_playwright": True}
        try:
            page = self.session._page
            if selector:
                with page.expect_download(timeout=30000) as dl_info:
                    page.click(selector)
                download = dl_info.value
            elif url:
                # navigate triggers download for direct file URLs
                with page.expect_download(timeout=30000) as dl_info:
                    page.goto(url)
                download = dl_info.value
            else:
                return {"ok": False, "error": "Provide url or selector for download"}
            fname = download.suggested_filename or f"download_{uuid.uuid4().hex[:8]}"
            dest = self.download_dir / fname
            download.save_as(str(dest))
            rec = {"path": str(dest), "filename": fname, "url": url or self.session.current_url, "ts": time.time()}
            self.session.downloads.append(rec)
            return {"ok": True, "path": str(dest), "filename": fname, "message": f"Downloaded to {dest}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def upload_file(self, selector: str, file_path: str) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {"ok": False, "error": started.get("error") or "Browser not available", "needs_playwright": True}
        path = Path(file_path).expanduser()
        if not path.exists():
            return {"ok": False, "error": f"File not found: {path}"}
        try:
            self.session._page.set_input_files(selector, str(path))
            return {"ok": True, "message": f"Attached {path.name} to {selector}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def take_webpage_screenshot(self, dest: Optional[Path] = None) -> Dict[str, Any]:
        started = self.ensure_browser()
        if not started.get("ok") or self.session._page is None:
            return {"ok": False, "error": started.get("error") or "Browser not available", "needs_playwright": True}
        dest = Path(dest) if dest else self.download_dir / f"page_{uuid.uuid4().hex[:8]}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.session._page.screenshot(path=str(dest), full_page=True)
            return {"ok": True, "path": str(dest), "message": f"Screenshot saved to {dest}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_page(self, dest: Optional[Path] = None) -> Dict[str, Any]:
        started = self.ensure_browser()
        dest = Path(dest) if dest else self.download_dir / f"page_{uuid.uuid4().hex[:8]}.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not started.get("ok") or self.session._page is None:
            # save minimal stub from session
            dest.write_text(
                f"<html><head><title>{self.session.title}</title></head>"
                f"<body><p>Simulated save of {self.session.current_url}</p></body></html>",
                encoding="utf-8",
            )
            return {
                "ok": True,
                "simulated": True,
                "path": str(dest),
                "message": f"Saved stub page to {dest}",
            }
        try:
            content = self.session._page.content()
            dest.write_text(content, encoding="utf-8")
            return {"ok": True, "path": str(dest), "message": f"Saved page to {dest}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def history_list(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "history": list(self.session.history),
            "index": self.session.history_index,
            "current_url": self.session.current_url,
            "downloads": list(self.session.downloads),
        }
