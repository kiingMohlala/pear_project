"""Email connector – SMTP send / IMAP list (optional stdlib)."""

from __future__ import annotations

import smtplib
import imaplib
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class EmailConnector(Connector):
    name = "email"
    description = "Send email via SMTP and list inbox via IMAP"
    provider = "smtp_imap"
    capabilities = [
        ConnectorCapability("send", "Send email", "email_send", sensitive=True),
        ConnectorCapability("list_inbox", "List recent messages", "email_read"),
        ConnectorCapability("status", "Connection status", "email_read"),
    ]

    def __init__(self):
        super().__init__()
        self._creds: Dict[str, Any] = {}

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        if not credentials:
            return ConnectorResult(ok=False, error="Email credentials required (host, username, password)")
        self._creds = dict(credentials)
        return ConnectorResult(ok=True, message="Credentials accepted")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        creds = credentials or self._creds
        if not creds.get("username") or not creds.get("password"):
            self.status = ConnectorStatus.AUTH_REQUIRED
            return ConnectorResult(ok=False, error="username/password required")
        # Optional live check if smtp_host provided
        host = creds.get("smtp_host")
        if host and creds.get("verify"):
            try:
                port = int(creds.get("smtp_port") or 587)
                with smtplib.SMTP(host, port, timeout=10) as smtp:
                    smtp.starttls()
                    smtp.login(creds["username"], creds["password"])
            except Exception as e:
                return ConnectorResult(ok=False, error=f"SMTP auth failed: {e}")
        self._creds = creds
        return ConnectorResult(ok=True, message="Email authenticated")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "status":
            return ConnectorResult(
                ok=True,
                data={"status": self.status.value, "user": self._creds.get("username")},
            )
        if action == "send":
            return self._send(**params)
        if action == "list_inbox":
            return self._list_inbox(limit=int(params.get("limit") or 10))
        return ConnectorResult(ok=False, error=f"Unknown action: {action}")

    def _send(self, **params) -> ConnectorResult:
        to = params.get("to")
        subject = params.get("subject") or "(no subject)"
        body = params.get("body") or ""
        if not to:
            return ConnectorResult(ok=False, error="Missing 'to'")
        # Always mark sensitive — caller/workflow may require approval
        if not params.get("approved"):
            return ConnectorResult(
                ok=False,
                needs_approval=True,
                message=f"Send email to {to} requires approval",
                approval_payload={"action": "send", "to": to, "subject": subject, "body": body},
            )
        host = self._creds.get("smtp_host")
        if not host:
            # dry-run success when no host (tests / demo)
            return ConnectorResult(
                ok=True,
                message=f"[dry-run] Email to {to}: {subject}",
                data={"to": to, "subject": subject, "dry_run": True},
            )
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = self._creds.get("username", "")
            msg["To"] = to
            port = int(self._creds.get("smtp_port") or 587)
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                if self._creds.get("starttls", True):
                    smtp.starttls()
                smtp.login(self._creds["username"], self._creds["password"])
                smtp.send_message(msg)
            return ConnectorResult(ok=True, message=f"Sent to {to}")
        except Exception as e:
            return ConnectorResult(ok=False, error=str(e))

    def _list_inbox(self, limit: int = 10) -> ConnectorResult:
        host = self._creds.get("imap_host")
        if not host:
            return ConnectorResult(
                ok=True,
                message="No imap_host configured — empty inbox (dry-run)",
                data={"messages": []},
            )
        try:
            port = int(self._creds.get("imap_port") or 993)
            mail = imaplib.IMAP4_SSL(host, port)
            mail.login(self._creds["username"], self._creds["password"])
            mail.select("INBOX")
            typ, data = mail.search(None, "ALL")
            ids = data[0].split()[-limit:]
            messages: List[Dict[str, Any]] = []
            for i in reversed(ids):
                typ, msg_data = mail.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                header = msg_data[0][1].decode("utf-8", errors="replace") if msg_data else ""
                messages.append({"id": i.decode(), "header": header.strip()})
            mail.logout()
            return ConnectorResult(ok=True, data={"messages": messages})
        except Exception as e:
            return ConnectorResult(ok=False, error=str(e))
