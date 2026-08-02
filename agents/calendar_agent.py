"""
Calendar Agent (v1.60) – intelligent scheduling on the Calendar connector.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import Agent
from core.task import Task
from core.llm import BaseLLM, create_llm, EchoLLM


@dataclass
class CalEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    description: str = ""
    location: str = ""
    recurrence: str = ""  # daily|weekly|monthly|""
    reminder_minutes: int = 30
    source: str = "local"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "description": self.description,
            "location": self.location,
            "recurrence": self.recurrence,
            "reminder_minutes": self.reminder_minutes,
            "source": self.source,
        }


# ── lightweight NL datetime parsing (deterministic, no external deps) ─

_DAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def _parse_time_token(tok: str) -> Optional[Tuple[int, int]]:
    tok = tok.strip().lower()
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", tok)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    ap = m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not ap and h <= 7:  # bare "3" → 15:00 heuristic for meetings
        h += 12
    return h, mi


def parse_event_nl(text: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Extract title, start, end, recurrence from phrases like:
      schedule team standup tomorrow at 10am for 30 minutes
      meeting with Alice next Friday 2pm-3pm weekly
    """
    now = now or datetime.utcnow()
    lower = text.lower().strip()
    recurrence = ""
    for r in ("daily", "weekly", "monthly"):
        if r in lower:
            recurrence = r

    # duration
    duration_min = 60
    m = re.search(r"for\s+(\d+)\s*(minutes|mins|min|hours|hour|hrs|hr)", lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        duration_min = n * 60 if unit.startswith("h") else n

    # date base
    start_date = now.date()
    if "tomorrow" in lower:
        start_date = (now + timedelta(days=1)).date()
    elif "today" in lower:
        start_date = now.date()
    else:
        m = re.search(r"next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)", lower)
        if m:
            target = _DAYS[m.group(1)]
            delta = (target - now.weekday() + 7) % 7
            delta = delta or 7
            start_date = (now + timedelta(days=delta)).date()
        else:
            m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lower)
            if m:
                target = _DAYS[m.group(1)]
                delta = (target - now.weekday() + 7) % 7
                start_date = (now + timedelta(days=delta)).date()

    # time range 2pm-3pm or at 10am
    start_h, start_m = 9, 0
    end_h, end_m = None, None
    m = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*[-–to]+\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", lower)
    if m:
        t1 = _parse_time_token(m.group(1))
        t2 = _parse_time_token(m.group(2))
        if t1:
            start_h, start_m = t1
        if t2:
            end_h, end_m = t2
    else:
        m = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", lower)
        if m:
            t1 = _parse_time_token(m.group(1))
            if t1:
                start_h, start_m = t1

    start = datetime(start_date.year, start_date.month, start_date.day, start_h, start_m)
    if end_h is not None:
        end = datetime(start_date.year, start_date.month, start_date.day, end_h, end_m or 0)
    else:
        end = start + timedelta(minutes=duration_min)

    # title: strip scheduling noise
    title = text
    for pat in [
        r"\b(schedule|book|create|add)\s+(an?\s+)?(event|meeting|appointment)?\s*",
        r"\b(tomorrow|today|next\s+\w+|\bmonday\b|\btuesday\b|\bwednesday\b|\bthursday\b|\bfriday\b|\bsaturday\b|\bsunday\b)",
        r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*[-–to]+\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        r"\bfor\s+\d+\s*(?:minutes|mins|min|hours|hour|hrs|hr)",
        r"\b(daily|weekly|monthly)\b",
        r"\b(with\s+)",
    ]:
        title = re.sub(pat, " ", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" -:") or "Event"

    return {
        "title": title[:80],
        "start": start,
        "end": end,
        "recurrence": recurrence,
    }


def events_overlap(a: CalEvent, b: CalEvent) -> bool:
    return a.start < b.end and b.start < a.end


class CalendarAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="calendar",
            description=(
                "Manages calendar: schedule/edit/cancel events, detect conflicts, "
                "find free time, set reminders, and summarize agendas using the "
                "Calendar connector and semantic index."
            ),
            capabilities=[
                "calendar",
                "schedule",
                "agenda",
                "reminders",
                "free_busy",
                "meeting",
            ],
            allowed_tools=[],
            system_prompt="You are PEAR's Calendar Agent. Prefer clear times and surface conflicts.",
            **kwargs,
        )
        self.llm: BaseLLM = llm or create_llm()
        self.permissions.grant("chat")
        self.permissions.grant("calendar_read")
        self.permissions.grant("calendar_write")
        self.calendar_events: List[CalEvent] = []
        self.reminders: List[Dict[str, Any]] = []
        self._synced_at: Optional[float] = None

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "calendar", "schedule", "agenda", "meeting", "appointment",
            "free time", "free-time", "reminder", "reschedule", "cancel event",
            "what’s on", "whats on", "busy",
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

        if lower in ("calendar", "calendar help", "/calendar"):
            return self._help()

        if lower.startswith("sync") or "sync calendar" in lower:
            return self._sync(background="background" in lower)

        if lower in ("agenda", "/agenda", "today", "what's on", "whats on"):
            return self._agenda(days=1)
        m = re.search(r"agenda\s+(\d+)\s*days?", lower)
        if m:
            return self._agenda(days=int(m.group(1)))

        if "free time" in lower or "free-time" in lower or lower.startswith("free "):
            return self._free_time(text)

        if "reminder" in lower:
            return self._reminders(text)

        m = re.search(r"cancel\s+(?:event\s+)?(.+)", lower)
        if m and "cancel" in lower:
            return self._cancel(m.group(1).strip())

        if any(k in lower for k in ("schedule", "book", "create event", "add event", "meeting")):
            return self._schedule(text)

        if "conflict" in lower:
            return self._conflicts()

        if lower.startswith("search ") or "calendar search" in lower:
            q = re.sub(r"^(calendar\s+)?search\s+", "", lower).strip()
            return self._search(q)

        # default agenda
        if "calendar" in lower:
            return self._agenda(days=7)

        return self._help()

    def _help(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "reply": (
                "Calendar commands:\n"
                "• schedule <event> tomorrow at 10am for 30 minutes\n"
                "• agenda [N days] · free time tomorrow\n"
                "• cancel event <title> · reminders\n"
                "• sync calendar [background]\n"
                "• conflicts — show overlapping events"
            ),
            "action": "help",
        }

    def _sync(self, background: bool = False) -> Dict[str, Any]:
        if background and self.planner:
            r = self.planner.submit_job("calendar sync calendar foreground")
            return {"ok": True, "reply": f"Queued calendar sync {r.get('job_id')}", "action": "calendar_sync_queued"}

        with self._span("calendar.sync"):
            events = self._load_from_connector()
            if not events and not self.calendar_events:
                # seed a couple demo events for offline UX
                now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
                events = [
                    CalEvent(
                        id="evt_demo1",
                        title="Standup",
                        start=now + timedelta(hours=1),
                        end=now + timedelta(hours=1, minutes=15),
                        recurrence="daily",
                        reminder_minutes=10,
                    ),
                    CalEvent(
                        id="evt_demo2",
                        title="Deep work",
                        start=now + timedelta(hours=3),
                        end=now + timedelta(hours=5),
                        reminder_minutes=15,
                    ),
                ]
            if events:
                # merge by id
                by_id = {e.id: e for e in self.calendar_events}
                for e in events:
                    by_id[e.id] = e
                self.calendar_events = sorted(by_id.values(), key=lambda e: e.start)
            self._synced_at = time.time()
            self._index()
        return {
            "ok": True,
            "reply": f"Calendar synced — {len(self.calendar_events)} event(s).",
            "action": "calendar_sync",
            "count": len(self.calendar_events),
        }

    def _load_from_connector(self) -> List[CalEvent]:
        try:
            if not self.planner or not hasattr(self.planner, "connectors"):
                return []
            reg = self.planner.connectors
            if not reg.has("calendar"):
                return []
            reg.connect("calendar")
            result = reg.execute("calendar", "list_events")
            if not result.ok:
                return []
            out = []
            for raw in (result.data or {}).get("events") or []:
                try:
                    start = datetime.fromisoformat(str(raw.get("start")).replace("Z", ""))
                    end = datetime.fromisoformat(str(raw.get("end")).replace("Z", "")) if raw.get("end") else start + timedelta(hours=1)
                except Exception:
                    continue
                out.append(CalEvent(
                    id=raw.get("id") or f"evt_{uuid.uuid4().hex[:8]}",
                    title=raw.get("title") or "Event",
                    start=start,
                    end=end,
                    description=raw.get("description") or "",
                    source="connector",
                ))
            return out
        except Exception:
            return []

    def _persist_connector(self, event: CalEvent) -> None:
        try:
            if not self.planner or not hasattr(self.planner, "connectors"):
                return
            reg = self.planner.connectors
            if not reg.has("calendar"):
                return
            reg.connect("calendar")
            reg.execute(
                "calendar",
                "create_event",
                title=event.title,
                start=event.start.isoformat(),
                end=event.end.isoformat(),
                description=event.description,
            )
        except Exception:
            pass

    def _index(self) -> None:
        for e in self.calendar_events:
            try:
                self.memory.knowledge.add_document(
                    name=f"cal:{e.title[:50]}",
                    text=f"{e.title}\n{e.start.isoformat()} - {e.end.isoformat()}\n{e.description}",
                    source_path=f"calendar://{e.id}",
                    metadata={"type": "calendar_event", "start": e.start.isoformat()},
                )
            except Exception:
                pass

    def _schedule(self, text: str) -> Dict[str, Any]:
        with self._span("calendar.schedule"):
            parsed = parse_event_nl(text)
            event = CalEvent(
                id=f"evt_{uuid.uuid4().hex[:8]}",
                title=parsed["title"],
                start=parsed["start"],
                end=parsed["end"],
                recurrence=parsed.get("recurrence") or "",
                reminder_minutes=30,
            )
            conflicts = [e for e in self.calendar_events if events_overlap(event, e)]
            # expand recurrence (next 4 occurrences for local list)
            to_add = [event]
            if event.recurrence == "daily":
                for i in range(1, 4):
                    to_add.append(CalEvent(
                        id=f"{event.id}_{i}",
                        title=event.title,
                        start=event.start + timedelta(days=i),
                        end=event.end + timedelta(days=i),
                        recurrence="daily",
                    ))
            elif event.recurrence == "weekly":
                for i in range(1, 4):
                    to_add.append(CalEvent(
                        id=f"{event.id}_{i}",
                        title=event.title,
                        start=event.start + timedelta(weeks=i),
                        end=event.end + timedelta(weeks=i),
                        recurrence="weekly",
                    ))
            self.calendar_events.extend(to_add)
            self.calendar_events.sort(key=lambda e: e.start)
            self._persist_connector(event)
            self._index()
            # default reminder
            self.reminders.append({
                "event_id": event.id,
                "title": event.title,
                "when": (event.start - timedelta(minutes=event.reminder_minutes)).isoformat(),
                "minutes_before": event.reminder_minutes,
            })
        msg = (
            f"Scheduled **{event.title}**\n"
            f"{event.start.isoformat()} → {event.end.isoformat()}"
        )
        if event.recurrence:
            msg += f"\nRecurrence: {event.recurrence}"
        if conflicts:
            msg += "\n⚠ Conflicts:\n" + "\n".join(
                f"  • {c.title} ({c.start.strftime('%H:%M')}–{c.end.strftime('%H:%M')})" for c in conflicts
            )
        else:
            msg += "\nNo conflicts detected."
        return {
            "ok": True,
            "reply": msg,
            "action": "schedule",
            "event": event.to_dict(),
            "conflicts": [c.to_dict() for c in conflicts],
        }

    def _agenda(self, days: int = 1) -> Dict[str, Any]:
        if not self.calendar_events:
            self._sync()
        with self._span("calendar.summary", days=days):
            now = datetime.utcnow()
            until = now + timedelta(days=days)
            upcoming = [e for e in self.calendar_events if e.end >= now and e.start <= until]
            upcoming.sort(key=lambda e: e.start)
        lines = [f"## Agenda (next {days} day(s))\n"]
        if not upcoming:
            lines.append("No upcoming events.")
        for e in upcoming:
            lines.append(
                f"• **{e.title}**  \n"
                f"  {e.start.strftime('%Y-%m-%d %H:%M')} – {e.end.strftime('%H:%M')}"
                + (f" ({e.recurrence})" if e.recurrence else "")
            )
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "agenda",
            "events": [e.to_dict() for e in upcoming],
        }

    def _free_time(self, text: str) -> Dict[str, Any]:
        if not self.calendar_events:
            self._sync()
        with self._span("calendar.schedule", mode="free_busy"):
            day = datetime.utcnow().date()
            if "tomorrow" in text.lower():
                day = (datetime.utcnow() + timedelta(days=1)).date()
            work_start = datetime(day.year, day.month, day.day, 9, 0)
            work_end = datetime(day.year, day.month, day.day, 17, 0)
            day_events = sorted(
                [e for e in self.calendar_events if e.start.date() == day or e.end.date() == day],
                key=lambda e: e.start,
            )
            free: List[Tuple[datetime, datetime]] = []
            cursor = work_start
            for e in day_events:
                if e.start > cursor:
                    free.append((cursor, min(e.start, work_end)))
                cursor = max(cursor, e.end)
            if cursor < work_end:
                free.append((cursor, work_end))
            free = [(a, b) for a, b in free if (b - a).total_seconds() >= 1800]
        lines = [f"## Free time on {day.isoformat()} (≥30m)\n"]
        if not free:
            lines.append("No free slots in work hours.")
        for a, b in free:
            lines.append(f"• {a.strftime('%H:%M')} – {b.strftime('%H:%M')}")
        return {"ok": True, "reply": "\n".join(lines), "action": "free_time", "slots": [
            {"start": a.isoformat(), "end": b.isoformat()} for a, b in free
        ]}

    def _conflicts(self) -> Dict[str, Any]:
        conflicts = []
        sorted_e = sorted(self.calendar_events, key=lambda e: e.start)
        for i, a in enumerate(sorted_e):
            for b in sorted_e[i + 1:]:
                if b.start >= a.end:
                    break
                if events_overlap(a, b):
                    conflicts.append((a, b))
        lines = ["## Conflicts\n"]
        if not conflicts:
            lines.append("None.")
        for a, b in conflicts[:10]:
            lines.append(f"• {a.title} ↔ {b.title}")
        return {"ok": True, "reply": "\n".join(lines), "action": "conflicts", "count": len(conflicts)}

    def _cancel(self, key: str) -> Dict[str, Any]:
        key_l = key.lower()
        before = len(self.calendar_events)
        self.calendar_events = [e for e in self.calendar_events if key_l not in e.title.lower() and key_l not in e.id.lower()]
        removed = before - len(self.calendar_events)
        return {
            "ok": True,
            "reply": f"Cancelled {removed} event(s) matching “{key}”.",
            "action": "cancel",
            "removed": removed,
        }

    def _reminders(self, text: str) -> Dict[str, Any]:
        with self._span("calendar.reminder"):
            if "set reminder" in text.lower() or "add reminder" in text.lower():
                # attach to next event
                if not self.calendar_events:
                    self._sync()
                upcoming = [e for e in self.calendar_events if e.start >= datetime.utcnow()]
                if upcoming:
                    e = upcoming[0]
                    rem = {
                        "event_id": e.id,
                        "title": e.title,
                        "when": (e.start - timedelta(minutes=15)).isoformat(),
                        "minutes_before": 15,
                    }
                    self.reminders.append(rem)
                    return {
                        "ok": True,
                        "reply": f"Reminder set for **{e.title}** 15 minutes before.",
                        "action": "reminder_set",
                        "reminder": rem,
                    }
            lines = ["## Reminders\n"]
            if not self.reminders:
                lines.append("No reminders.")
            for r in self.reminders[-10:]:
                lines.append(f"• {r['title']} at {r['when']} ({r['minutes_before']}m before)")
        return {"ok": True, "reply": "\n".join(lines), "action": "reminders"}

    def _search(self, query: str) -> Dict[str, Any]:
        with self._span("calendar.search", query=query[:80]):
            q = query.lower()
            local = [e for e in self.calendar_events if q in e.title.lower() or q in e.description.lower()]
            try:
                sem = self.memory.knowledge.search(query, limit=3)
            except Exception:
                sem = []
        lines = [f"## Calendar search: {query}\n"]
        for e in local[:8]:
            lines.append(f"• {e.title} — {e.start.isoformat()}")
        if not local:
            lines.append("No local matches.")
        return {"ok": True, "reply": "\n".join(lines), "action": "calendar_search", "count": len(local)}
