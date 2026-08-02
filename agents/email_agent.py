"""
Email Agent (v1.50) – intelligent inbox assistant on top of the Email connector.

Offline/demo mode synthesizes a deterministic mailbox when IMAP is unavailable.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .base import Agent
from core.task import Task
from core.llm import BaseLLM, create_llm, EchoLLM


@dataclass
class EmailMessage:
    id: str
    thread_id: str
    from_addr: str
    to_addr: str
    subject: str
    body: str
    date: str
    labels: List[str] = field(default_factory=list)
    priority: float = 0.5  # 0–1 higher = more urgent
    attachments: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def priority_score(msg: EmailMessage) -> float:
    score = 0.4
    subj = (msg.subject or "").lower()
    body = (msg.body or "").lower()
    from_a = (msg.from_addr or "").lower()
    # urgency keywords
    for kw, w in (
        ("urgent", 0.25), ("asap", 0.2), ("action required", 0.25),
        ("deadline", 0.15), ("invoice", 0.1), ("meeting", 0.08),
        ("follow up", 0.12), ("follow-up", 0.12),
    ):
        if kw in subj or kw in body:
            score += w
    if "noreply" in from_a or "no-reply" in from_a or "newsletter" in from_a:
        score -= 0.25
    if msg.attachments:
        score += 0.05
    return max(0.0, min(1.0, score))


def demo_mailbox() -> List[EmailMessage]:
    now = datetime.utcnow()
    samples = [
        ("boss@acme.com", "you@pear.local", "URGENT: Q3 budget approval needed",
         "Please review the attached budget and approve by Friday. Action required.",
         ["inbox", "important"], ["budget.xlsx"]),
        ("alice@acme.com", "you@pear.local", "Re: Project Phoenix kickoff",
         "Thanks for the notes. I'll follow up with the design team tomorrow.",
         ["inbox"], []),
        ("alice@acme.com", "you@pear.local", "Project Phoenix kickoff",
         "Kickoff is scheduled for Monday 10am. Agenda attached.",
         ["inbox"], ["agenda.pdf"]),
        ("news@newsletter.com", "you@pear.local", "Your weekly tech digest",
         "Top stories this week in AI and cloud. Unsubscribe below.",
         ["inbox", "promo"], []),
        ("billing@vendor.com", "you@pear.local", "Invoice #4421 due soon",
         "Invoice total R4,200. Payment due in 7 days.",
         ["inbox"], ["invoice_4421.pdf"]),
        ("bob@partner.org", "you@pear.local", "Follow-up on partnership proposal",
         "Circling back on our conversation last week. Can we schedule a call?",
         ["inbox"], []),
    ]
    msgs: List[EmailMessage] = []
    for i, (frm, to, subj, body, labels, atts) in enumerate(samples):
        # thread by normalized subject
        thread_key = re.sub(r"^(re:|fwd:)\s*", "", subj, flags=re.I).strip().lower()
        tid = f"thr_{hash(thread_key) % 10_000_000:x}"
        msg = EmailMessage(
            id=f"msg_{i+1}",
            thread_id=tid,
            from_addr=frm,
            to_addr=to,
            subject=subj,
            body=body,
            date=(now - timedelta(hours=i * 5)).isoformat() + "Z",
            labels=labels,
            attachments=atts,
        )
        msg.priority = priority_score(msg)
        msgs.append(msg)
    return msgs


class EmailAgent(Agent):
    def __init__(self, llm: Optional[BaseLLM] = None, **kwargs):
        super().__init__(
            name="email",
            description=(
                "Manages email: sync inbox, prioritize, search, summarize threads, "
                "detect follow-ups, and draft replies using the Email connector and "
                "semantic knowledge index."
            ),
            capabilities=[
                "email",
                "inbox",
                "email_search",
                "email_summarize",
                "email_compose",
                "email_priority",
            ],
            allowed_tools=["summarize_text"],
            system_prompt=(
                "You are PEAR's Email Agent. Be concise, preserve intent, and never "
                "send mail without explicit approval."
            ),
            **kwargs,
        )
        self.llm: BaseLLM = llm or create_llm()
        self.permissions.grant("chat")
        self.permissions.grant("email_read")
        self.permissions.grant("email_send")
        self.mailbox: List[EmailMessage] = []
        self.last_draft: str = ""
        self._synced_at: Optional[float] = None

    def can_handle(self, task: Task) -> float:
        obj = task.objective.lower()
        score = super().can_handle(task)
        signals = [
            "inbox", "email", "mail ", "draft", "reply to", "summarize thread",
            "follow-up", "follow up", "unread", "send email", "compose",
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

        if lower in ("inbox", "/inbox", "show inbox", "list inbox"):
            return self._inbox()

        if lower.startswith("sync") or "sync inbox" in lower or "sync email" in lower:
            return self._sync(background="background" in lower or "job" in lower)

        m = re.search(r"(?:email[- ]?search|search\s+mail|search\s+email)\s+(.+)", lower)
        if m:
            return self._search(m.group(1).strip())

        m = re.search(r"summarize\s+thread\s+(.+)", lower)
        if m:
            return self._summarize_thread(m.group(1).strip())
        if "summarize thread" in lower:
            # default: highest priority thread
            if not self.mailbox:
                self._sync()
            if self.mailbox:
                return self._summarize_thread(self.mailbox[0].thread_id)

        m = re.search(r"(?:draft|compose)\s+(?:email|reply|mail)\s*(.*)", lower)
        if m:
            return self._draft(m.group(1).strip() or "reply to latest important message")

        if "follow-up" in lower or "follow up" in lower:
            return self._followups()

        if lower in ("email help", "email", "/email"):
            return self._help()

        # default: treat as search
        if "email" in lower or "mail" in lower:
            return self._search(re.sub(r"\b(email|mail)\b", "", lower).strip() or "inbox")

        return self._help()

    def _help(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "reply": (
                "Email commands:\n"
                "• sync inbox [background]\n"
                "• inbox — prioritized view\n"
                "• email search <query>\n"
                "• summarize thread <id|subject>\n"
                "• draft email <intent> · draft reply\n"
                "• follow-ups — detect pending follow-ups\n"
                "Sending always requires connector approval."
            ),
            "action": "help",
        }

    def _sync(self, background: bool = False) -> Dict[str, Any]:
        if background and self.planner:
            r = self.planner.submit_job("email sync inbox foreground")
            return {
                "ok": True,
                "reply": f"Queued inbox sync job {r.get('job_id')}",
                "action": "email_sync_queued",
                "job_id": r.get("job_id"),
            }

        with self._span("email.sync"):
            messages: List[EmailMessage] = []
            # Try connector IMAP list
            conn_msgs = self._connector_list()
            if conn_msgs:
                messages = conn_msgs
            else:
                messages = demo_mailbox()

            for msg in messages:
                msg.priority = priority_score(msg)
            messages.sort(key=lambda m: m.priority, reverse=True)
            self.mailbox = messages
            self._synced_at = time.time()

        with self._span("email.index", count=len(messages)):
            self._index_messages(messages)

        return {
            "ok": True,
            "reply": f"Synced {len(messages)} messages." + (
                f" Top: {messages[0].subject}" if messages else ""
            ),
            "action": "email_sync",
            "count": len(messages),
        }

    def _connector_list(self) -> List[EmailMessage]:
        try:
            if not self.planner or not hasattr(self.planner, "connectors"):
                return []
            reg = self.planner.connectors
            if not reg.has("email"):
                return []
            # ensure connected with dry-run creds if needed
            st = reg.get("email")
            if st.status.value != "connected":
                reg.connect("email", {
                    "username": "you@pear.local",
                    "password": "demo",
                })
            result = reg.execute("email", "list_inbox", limit=20)
            if not result.ok:
                return []
            raw = (result.data or {}).get("messages") or []
            # IMAP headers only in real mode — fall back if empty
            if not raw:
                return []
            out = []
            for i, m in enumerate(raw):
                header = m.get("header") or ""
                out.append(EmailMessage(
                    id=m.get("id") or f"imap_{i}",
                    thread_id=f"thr_{i}",
                    from_addr="",
                    to_addr="",
                    subject=header[:80] or "(no subject)",
                    body=header,
                    date="",
                    labels=["inbox"],
                ))
            return out
        except Exception:
            return []

    def _index_messages(self, messages: List[EmailMessage]) -> None:
        for msg in messages:
            try:
                text = f"From: {msg.from_addr}\nSubject: {msg.subject}\n\n{msg.body}"
                self.memory.knowledge.add_document(
                    name=f"email:{msg.subject[:50]}",
                    text=text,
                    source_path=f"email://{msg.id}",
                    metadata={
                        "type": "email",
                        "thread_id": msg.thread_id,
                        "from": msg.from_addr,
                        "priority": msg.priority,
                        "attachments": msg.attachments,
                    },
                )
                # attachments → media/document pipeline names only
                for att in msg.attachments:
                    self.memory.knowledge.add_note(
                        f"attachment:{att}",
                        f"Email {msg.id} attachment {att} (subject={msg.subject})",
                    )
            except Exception:
                pass

    def _inbox(self) -> Dict[str, Any]:
        if not self.mailbox:
            self._sync()
        lines = ["## Inbox (priority order)\n"]
        for msg in self.mailbox[:15]:
            flag = "🔴" if msg.priority >= 0.7 else ("🟡" if msg.priority >= 0.5 else "⚪")
            att = f" 📎{len(msg.attachments)}" if msg.attachments else ""
            lines.append(
                f"{flag} **{msg.subject}**\n"
                f"   from {msg.from_addr} · pri={msg.priority:.2f} · {msg.id}{att}"
            )
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "inbox",
            "messages": [m.to_dict() for m in self.mailbox[:15]],
        }

    def _search(self, query: str) -> Dict[str, Any]:
        if not self.mailbox:
            self._sync()
        with self._span("email.search", query=query[:80]):
            # semantic + keyword
            hits = []
            try:
                sem = self.memory.knowledge.search(query, limit=5)
                for h in sem:
                    hits.append({
                        "source": "semantic",
                        "title": h.get("title"),
                        "snippet": h.get("snippet"),
                    })
            except Exception:
                pass
            q = query.lower()
            local = [
                m for m in self.mailbox
                if q in m.subject.lower() or q in m.body.lower() or q in m.from_addr.lower()
            ]
        lines = [f"## Email search: {query}\n"]
        for m in local[:8]:
            lines.append(f"• {m.subject} — {m.from_addr} ({m.id})")
        if hits:
            lines.append("\nSemantic index:")
            for h in hits[:3]:
                lines.append(f"• {h.get('title')}: {str(h.get('snippet'))[:100]}")
        if not local and not hits:
            lines.append("No matches.")
        return {
            "ok": True,
            "reply": "\n".join(lines),
            "action": "email_search",
            "count": len(local),
        }

    def _thread_messages(self, key: str) -> List[EmailMessage]:
        key_l = key.lower()
        thr = [m for m in self.mailbox if m.thread_id == key or key_l in m.thread_id.lower()]
        if not thr:
            thr = [
                m for m in self.mailbox
                if key_l in m.subject.lower() or key_l in re.sub(r"^(re:|fwd:)\s*", "", m.subject, flags=re.I).lower()
            ]
        thr.sort(key=lambda m: m.date)
        return thr

    def _summarize_thread(self, key: str) -> Dict[str, Any]:
        if not self.mailbox:
            self._sync()
        with self._span("email.summarize", thread=key[:40]):
            thr = self._thread_messages(key)
            if not thr:
                return {"ok": False, "reply": f"No thread matching “{key}”", "action": "summarize_thread"}
            blob = "\n---\n".join(
                f"From: {m.from_addr}\nSubject: {m.subject}\n{m.body}" for m in thr
            )
            summary = self._summarize_text(blob, thr[0].subject)
        return {
            "ok": True,
            "reply": f"## Thread: {thr[0].subject}\nMessages: {len(thr)}\n\n{summary}",
            "action": "summarize_thread",
            "thread_id": thr[0].thread_id,
            "count": len(thr),
        }

    def _summarize_text(self, text: str, subject: str) -> str:
        if self._llm_usable():
            try:
                resp = self.llm.chat(
                    self.system_prompt,
                    f"Summarize this email thread ({subject}) in 3-5 bullets:\n\n{text[:6000]}",
                )
                return (resp.content or "").strip()
            except Exception:
                pass
        # extractive
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:6]
        return "Summary:\n" + "\n".join(f"• {ln[:120]}" for ln in lines)

    def _followups(self) -> Dict[str, Any]:
        if not self.mailbox:
            self._sync()
        pending = []
        for m in self.mailbox:
            blob = f"{m.subject} {m.body}".lower()
            if any(k in blob for k in ("follow up", "follow-up", "circling back", "action required", "due")):
                pending.append(m)
        pending.sort(key=lambda m: m.priority, reverse=True)
        lines = ["## Follow-ups detected\n"]
        for m in pending[:10]:
            lines.append(f"• {m.subject} — {m.from_addr} (pri={m.priority:.2f})")
        if not pending:
            lines.append("None detected.")
        return {"ok": True, "reply": "\n".join(lines), "action": "followups", "count": len(pending)}

    def _draft(self, intent: str) -> Dict[str, Any]:
        if not self.mailbox:
            self._sync()
        with self._span("email.compose", intent=intent[:80]):
            # pick target message
            target = self.mailbox[0] if self.mailbox else None
            if "reply" in intent.lower() and self.mailbox:
                target = max(self.mailbox, key=lambda m: m.priority)
            if self._llm_usable() and target:
                try:
                    resp = self.llm.chat(
                        self.system_prompt,
                        f"Draft a professional email.\nIntent: {intent}\n"
                        f"In reply to from={target.from_addr} subject={target.subject}\n"
                        f"Body: {target.body}\n"
                        "Include Subject and Body.",
                    )
                    draft = (resp.content or "").strip()
                except Exception:
                    draft = self._template_draft(intent, target)
            else:
                draft = self._template_draft(intent, target)
            self.last_draft = draft
        return {
            "ok": True,
            "reply": f"## Draft (not sent)\n\n{draft}\n\n_Send via connector with approval._",
            "action": "draft_email",
            "draft": draft,
        }

    def _template_draft(self, intent: str, target: Optional[EmailMessage]) -> str:
        if target and "reply" in intent.lower():
            return (
                f"Subject: Re: {re.sub(r'^(Re:\\s*)+', '', target.subject, flags=re.I)}\n\n"
                f"Hi,\n\nThank you for your message regarding \"{target.subject}\". "
                f"I will review and get back to you shortly.\n\nBest regards"
            )
        return (
            f"Subject: {intent[:60] or 'Follow-up'}\n\n"
            f"Hi,\n\n{intent or 'I wanted to follow up on our recent conversation.'}\n\nBest regards"
        )

    def _llm_usable(self) -> bool:
        return not isinstance(self.llm, EchoLLM) and getattr(self.llm, "provider", "") not in ("echo", "")
