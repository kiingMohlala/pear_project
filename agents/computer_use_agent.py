"""
Computer Use Agent (v1.40) – observe GUI, locate elements, act with approvals.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Agent
from core.task import Task
from core.computer import ComputerController, UIElement, locate_elements_from_ocr
from core.media import MediaManager, OfflineVision, create_vision


# Actions that always need approval
DESTRUCTIVE = {"delete", "remove", "format", "shutdown", "rm ", "empty trash"}


class ComputerUseAgent(Agent):
    def __init__(
        self,
        controller: Optional[ComputerController] = None,
        media: Optional[MediaManager] = None,
        **kwargs,
    ):
        super().__init__(
            name="computer",
            description=(
                "Operates graphical desktop apps: capture UI, locate elements via OCR/"
                "vision, click, type, scroll, drag, and manage windows. Destructive "
                "actions require approval."
            ),
            capabilities=[
                "computer_use",
                "gui",
                "ui_automation",
                "click",
                "type",
                "window_management",
            ],
            allowed_tools=["take_screenshot", "get_system_info"],
            system_prompt="You are PEAR's Computer Use Agent. Observe before acting; confirm destructive steps.",
            **kwargs,
        )
        self.controller = controller or ComputerController()
        self.media = media or MediaManager(vision=create_vision("offline"))
        self.permissions.grant("chat")
        for a in ("computer_observe", "computer_act", "computer_type", "computer_window"):
            self.permissions.grant(a)
        self.last_elements: List[UIElement] = []
        self.last_capture: Optional[str] = None
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "click", "double click", "type into", "press keys", "hotkey",
            "scroll", "drag", "capture ui", "find button", "ui element",
            "computer use", "focus window", "gui ",
        ]
        if any(s in obj for s in signals):
            score = max(score, 0.88)
        return score

    def _span(self, name: str, **attrs):
        try:
            from core.tracing import get_tracer
            return get_tracer().span(name, kind="agent", **attrs)
        except Exception:
            from contextlib import nullcontext
            return nullcontext()

    def _process(self, task: Task, **kwargs) -> Dict[str, Any]:
        text = task.objective
        lower = text.lower().strip()

        m = re.match(r"approve\s+computer\s+(\w+)", lower)
        if m:
            return self._approve(m.group(1))

        if lower in ("computer", "computer help", "/computer"):
            return self._help()

        if "capture ui" in lower or lower in ("capture-ui", "/capture-ui"):
            return self._observe()

        m = re.search(r"(?:find|locate)\s+(?:button\s+|element\s+|ui\s+)?(.+)", lower)
        if m and any(k in lower for k in ("find", "locate")):
            return self._locate(m.group(1).strip().rstrip("."))

        m = re.search(r"(?:double[- ]?)?click(?:\s+on)?\s+(.+)", lower)
        if m:
            return self._click_target(m.group(1).strip().rstrip("."), double="double" in lower)

        m = re.search(r"type\s+(.+?)(?:\s+into\s+(.+))?$", lower)
        if m and lower.startswith("type"):
            return self._type(m.group(1).strip().strip("'\""), m.group(2))

        m = re.search(r"hotkey\s+(.+)", lower)
        if m:
            keys = [k.strip() for k in re.split(r"[+\s]+", m.group(1)) if k.strip()]
            return self._hotkey(keys)

        m = re.search(r"scroll\s+(-?\d+)", lower)
        if m:
            with self._span("computer.act", action="scroll"):
                r = self.controller.scroll(int(m.group(1)))
            return {"ok": r["ok"], "reply": r.get("message"), "action": "scroll", "data": r}

        m = re.search(r"(?:focus|activate)\s+window\s+(.+)", lower)
        if m:
            with self._span("computer.act", action="window_focus"):
                r = self.controller.window_focus(m.group(1).strip())
            return {"ok": r["ok"], "reply": r.get("message"), "action": "window_focus", "data": r}

        m = re.search(r"drag\s+\(?\s*(\d+)\s*,\s*(\d+)\s*\)?\s+to\s+\(?\s*(\d+)\s*,\s*(\d+)\s*\)?", lower)
        if m:
            return self._drag(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))

        m = re.search(r"click\s+(?:at\s+)?(\d+)\s*,\s*(\d+)", lower)
        if m:
            return self._click_xy(int(m.group(1)), int(m.group(2)))

        return self._help()

    def _help(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "reply": (
                "Computer Use commands:\n"
                "• capture ui — screenshot + OCR element map\n"
                "• find button <label> / locate <text>\n"
                "• click <label> | click at x,y | double click <label>\n"
                "• type <text> [into <field>]\n"
                "• hotkey ctrl+s · scroll <n> · drag x1,y1 to x2,y2\n"
                "• focus window <title>\n"
                f"Backend: {self.controller.backend}\n"
                "Destructive actions need: approve computer <id>"
            ),
            "action": "help",
            "backend": self.controller.backend,
        }

    def _observe(self) -> Dict[str, Any]:
        with self._span("computer.observe"):
            self.permissions.require("computer_observe")
            cap = self.controller.capture()
            if not cap.get("ok"):
                return {"ok": False, "reply": cap.get("message"), "action": "observe", "data": cap}
            path = cap["params"].get("path")
            self.last_capture = path
            # OCR via media pipeline
            ocr_text = ""
            if path:
                # offline sidecar support
                p = Path(path)
                side = p.with_suffix(p.suffix + ".txt")
                if not side.exists():
                    # write demo OCR for sim captures so locate works offline
                    if cap.get("params", {}).get("simulated") or self.controller.backend == "sim":
                        side.write_text("File\nEdit\nView\nSave\nCancel\nOK\nSearch\nSettings\n", encoding="utf-8")
                ocr = self.media.ocr(path)
                ocr_text = ((ocr.get("vision") or {}).get("text") or "")
            elements = locate_elements_from_ocr(ocr_text)
            self.last_elements = elements
        lines = [f"Captured UI → {path}", f"Elements ({len(elements)}):"]
        for el in elements[:12]:
            lines.append(f"  • {el.label} @ ({el.x},{el.y})")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "observe",
            "elements": [e.to_dict() for e in elements],
            "path": path,
        }

    def _locate(self, query: str) -> Dict[str, Any]:
        with self._span("computer.locate", query=query):
            if not self.last_elements:
                obs = self._observe()
                if not obs.get("ok"):
                    return obs
            matches = [e for e in self.last_elements if query.lower() in e.label.lower()]
            if not matches:
                # re-score
                matches = locate_elements_from_ocr(
                    "\n".join(e.label for e in self.last_elements),
                    query=query,
                )
                matches = [m for m in matches if m.score >= 0.5]
            self.last_elements = matches or self.last_elements
        if not matches:
            return {"ok": False, "reply": f"No UI element matching “{query}”", "action": "locate"}
        el = matches[0]
        return {
            "ok": True,
            "reply": f"Found **{el.label}** at ({el.x},{el.y}) score={el.score:.2f}",
            "action": "locate",
            "element": el.to_dict(),
        }

    def _needs_approval(self, description: str) -> bool:
        d = description.lower()
        return any(k in d for k in DESTRUCTIVE)

    def _click_target(self, target: str, double: bool = False) -> Dict[str, Any]:
        # coordinate form
        m = re.match(r"(\d+)\s*,\s*(\d+)$", target)
        if m:
            return self._click_xy(int(m.group(1)), int(m.group(2)), double=double)

        loc = self._locate(target)
        if not loc.get("ok"):
            return loc
        el = loc["element"]
        if self._needs_approval(target):
            return self._queue_approval("click", x=el["x"], y=el["y"], label=el["label"], double=double)
        return self._click_xy(el["x"], el["y"], double=double, label=el["label"])

    def _click_xy(self, x: int, y: int, double: bool = False, label: str = "") -> Dict[str, Any]:
        with self._span("computer.act", action="click", x=x, y=y):
            self.permissions.require("computer_act")
            r = self.controller.double_click(x, y) if double else self.controller.click(x, y)
        with self._span("computer.verify"):
            verified = r.get("ok", False)
        return {
            "ok": verified,
            "reply": r.get("message") + (f" [{label}]" if label else ""),
            "action": "click",
            "data": r,
        }

    def _type(self, text: str, field: Optional[str]) -> Dict[str, Any]:
        if field:
            loc = self._locate(field)
            if loc.get("ok"):
                el = loc["element"]
                self._click_xy(el["x"], el["y"], label=el["label"])
        if self._needs_approval(text):
            return self._queue_approval("type", text=text)
        with self._span("computer.act", action="type"):
            self.permissions.require("computer_type")
            r = self.controller.type_text(text)
        return {"ok": r["ok"], "reply": r.get("message"), "action": "type", "data": r}

    def _hotkey(self, keys: List[str]) -> Dict[str, Any]:
        with self._span("computer.act", action="hotkey"):
            r = self.controller.hotkey(*keys)
        return {"ok": r["ok"], "reply": r.get("message"), "action": "hotkey", "data": r}

    def _drag(self, x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
        with self._span("computer.act", action="drag"):
            self.permissions.require("computer_act")
            r = self.controller.drag(x1, y1, x2, y2)
        return {"ok": r["ok"], "reply": r.get("message"), "action": "drag", "data": r}

    def _queue_approval(self, action: str, **params) -> Dict[str, Any]:
        aid = f"cu_{abs(hash(str(params))) % 10_000_000}"
        self.pending_approvals[aid] = {"action": action, **params}
        return {
            "ok": True,
            "reply": f"Approval required for {action} {params}.\nSay: approve computer {aid}",
            "action": "needs_approval",
            "approval_id": aid,
        }

    def _approve(self, approval_id: str) -> Dict[str, Any]:
        pending = self.pending_approvals.pop(approval_id, None)
        if not pending:
            return {"ok": False, "reply": f"Unknown approval {approval_id}", "action": "approve"}
        action = pending.pop("action")
        if action == "click":
            return self._click_xy(pending["x"], pending["y"], double=pending.get("double", False), label=pending.get("label", ""))
        if action == "type":
            with self._span("computer.act", action="type"):
                r = self.controller.type_text(pending.get("text", ""))
            return {"ok": r["ok"], "reply": r.get("message"), "action": "type", "data": r}
        return {"ok": False, "reply": f"Unsupported {action}", "action": "approve"}
