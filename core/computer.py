"""
Computer-use primitives (v1.40) – mouse, keyboard, window, UI observation.

Best-effort backends:
  - pyautogui / pynput when available
  - offline simulation recording actions for tests / headless CI
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _backend() -> str:
    if os.environ.get("PEAR_COMPUTER_BACKEND") == "sim":
        return "sim"
    try:
        import pyautogui  # noqa: F401
        return "pyautogui"
    except Exception:
        return "sim"


@dataclass
class UIElement:
    id: str
    label: str
    x: int
    y: int
    w: int = 0
    h: int = 0
    score: float = 0.0
    source: str = "ocr"  # ocr | vision | sim

    def center(self) -> Tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionLog:
    action: str
    params: Dict[str, Any]
    ok: bool
    message: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class ComputerController:
    """Low-level GUI control with simulation fallback."""

    def __init__(self, screenshot_dir: Optional[Path] = None):
        self.backend = _backend()
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else Path.home() / "PEAR_Workspace" / "ui_captures"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.history: List[ActionLog] = []
        self._screen_size = (1920, 1080)
        if self.backend == "pyautogui":
            try:
                import pyautogui
                pyautogui.FAILSAFE = True
                self._screen_size = pyautogui.size()
            except Exception:
                self.backend = "sim"

    def _log(self, action: str, ok: bool, message: str = "", **params) -> Dict[str, Any]:
        entry = ActionLog(action=action, params=params, ok=ok, message=message)
        self.history.append(entry)
        return {"ok": ok, "action": action, "message": message, "params": params, "backend": self.backend}

    def capture(self, path: Optional[Path] = None) -> Dict[str, Any]:
        dest = Path(path) if path else self.screenshot_dir / f"ui_{uuid.uuid4().hex[:8]}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.backend == "pyautogui":
            try:
                import pyautogui
                img = pyautogui.screenshot()
                img.save(str(dest))
                return self._log("capture", True, f"Saved {dest}", path=str(dest), size=list(self._screen_size))
            except Exception as e:
                return self._log("capture", False, str(e))
        # sim: write tiny PNG header stub
        dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return self._log("capture", True, f"Simulated capture {dest}", path=str(dest), simulated=True)

    def move(self, x: int, y: int) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pyautogui
                pyautogui.moveTo(x, y, duration=0.15)
                return self._log("move", True, f"Moved to ({x},{y})", x=x, y=y)
            except Exception as e:
                return self._log("move", False, str(e), x=x, y=y)
        return self._log("move", True, f"[sim] move ({x},{y})", x=x, y=y, simulated=True)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pyautogui
                pyautogui.click(x, y, clicks=clicks, button=button)
                return self._log("click", True, f"Clicked ({x},{y})", x=x, y=y, button=button)
            except Exception as e:
                return self._log("click", False, str(e), x=x, y=y)
        return self._log("click", True, f"[sim] click ({x},{y})", x=x, y=y, simulated=True)

    def double_click(self, x: int, y: int) -> Dict[str, Any]:
        return self.click(x, y, clicks=2)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pyautogui
                pyautogui.moveTo(x1, y1)
                pyautogui.dragTo(x2, y2, duration=0.3)
                return self._log("drag", True, f"Drag ({x1},{y1})→({x2},{y2})", x1=x1, y1=y1, x2=x2, y2=y2)
            except Exception as e:
                return self._log("drag", False, str(e))
        return self._log("drag", True, f"[sim] drag", x1=x1, y1=y1, x2=x2, y2=y2, simulated=True)

    def type_text(self, text: str, interval: float = 0.02) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pyautogui
                pyautogui.typewrite(text, interval=interval)
                return self._log("type", True, f"Typed {len(text)} chars", length=len(text))
            except Exception as e:
                return self._log("type", False, str(e))
        return self._log("type", True, f"[sim] type {text[:40]}", text=text, simulated=True)

    def hotkey(self, *keys: str) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pyautogui
                pyautogui.hotkey(*keys)
                return self._log("hotkey", True, "+".join(keys), keys=list(keys))
            except Exception as e:
                return self._log("hotkey", False, str(e))
        return self._log("hotkey", True, f"[sim] hotkey {'+'.join(keys)}", keys=list(keys), simulated=True)

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pyautogui
                if x is not None and y is not None:
                    pyautogui.scroll(clicks, x=x, y=y)
                else:
                    pyautogui.scroll(clicks)
                return self._log("scroll", True, f"scroll {clicks}", clicks=clicks)
            except Exception as e:
                return self._log("scroll", False, str(e))
        return self._log("scroll", True, f"[sim] scroll {clicks}", clicks=clicks, simulated=True)

    def window_list(self) -> Dict[str, Any]:
        # Best-effort; sim returns empty
        if self.backend == "pyautogui":
            try:
                import pygetwindow as gw  # type: ignore
                wins = [{"title": w.title, "left": w.left, "top": w.top, "width": w.width, "height": w.height}
                        for w in gw.getAllWindows() if w.title]
                return self._log("window_list", True, f"{len(wins)} windows", windows=wins)
            except Exception:
                pass
        return self._log("window_list", True, "No window manager (sim)", windows=[], simulated=True)

    def window_focus(self, title_substr: str) -> Dict[str, Any]:
        if self.backend == "pyautogui":
            try:
                import pygetwindow as gw  # type: ignore
                for w in gw.getAllWindows():
                    if title_substr.lower() in (w.title or "").lower():
                        w.activate()
                        return self._log("window_focus", True, f"Focused {w.title}", title=w.title)
                return self._log("window_focus", False, f"No window matching {title_substr}")
            except Exception as e:
                return self._log("window_focus", False, str(e))
        return self._log("window_focus", True, f"[sim] focus {title_substr}", title=title_substr, simulated=True)


def locate_elements_from_ocr(
    ocr_text: str,
    screen_w: int = 1920,
    screen_h: int = 1080,
    query: str = "",
) -> List[UIElement]:
    """
    Heuristic: map OCR lines to pseudo-coordinates by line index.
    Real deployments should use bounding boxes from Tesseract/vision models.
    """
    lines = [ln.strip() for ln in (ocr_text or "").splitlines() if ln.strip()]
    if not lines:
        # synthetic demo elements for offline tests
        demo = ["File", "Edit", "Save", "Cancel", "OK", "Search"]
        lines = demo
    n = max(1, len(lines))
    elements: List[UIElement] = []
    q = query.lower().strip()
    for i, label in enumerate(lines[:40]):
        y = int((i + 1) * (screen_h / (n + 1)))
        x = int(screen_w * 0.15)
        score = 1.0
        if q:
            score = 1.0 if q in label.lower() else (0.5 if any(t in label.lower() for t in q.split()) else 0.1)
        elements.append(UIElement(
            id=f"el_{i}",
            label=label[:80],
            x=x,
            y=y,
            w=min(400, 20 * len(label)),
            h=24,
            score=score,
            source="ocr",
        ))
    if q:
        elements.sort(key=lambda e: e.score, reverse=True)
    return elements
