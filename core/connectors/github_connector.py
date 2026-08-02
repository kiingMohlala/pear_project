"""GitHub connector – REST API via stdlib urllib."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus


class GitHubConnector(Connector):
    name = "github"
    description = "GitHub REST API – list repos, issues; create issues with approval"
    provider = "github"
    capabilities = [
        ConnectorCapability("list_repos", "List repositories", "github_read"),
        ConnectorCapability("list_issues", "List issues", "github_read"),
        ConnectorCapability("create_issue", "Create issue", "github_write", sensitive=True),
        ConnectorCapability("me", "Authenticated user", "github_read"),
    ]

    def __init__(self):
        super().__init__()
        self._token: Optional[str] = None
        self._api = "https://api.github.com"

    def connect(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        if credentials and credentials.get("token"):
            self._token = credentials["token"]
            return ConnectorResult(ok=True, message="Token set")
        if self._token:
            return ConnectorResult(ok=True, message="Token present")
        return ConnectorResult(ok=False, error="GitHub token required")

    def authenticate(self, credentials: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        if credentials and credentials.get("token"):
            self._token = credentials["token"]
        if not self._token:
            self.status = ConnectorStatus.AUTH_REQUIRED
            return ConnectorResult(ok=False, error="Missing token")
        # lightweight auth check only when verify=True
        if credentials and credentials.get("verify"):
            r = self._request("GET", "/user")
            if not r.ok:
                return r
        return ConnectorResult(ok=True, message="GitHub token accepted")

    def execute(self, action: str, **params: Any) -> ConnectorResult:
        if action == "me":
            return self._request("GET", "/user")
        if action == "list_repos":
            user = params.get("user") or "user"
            path = "/user/repos" if user == "user" else f"/users/{user}/repos"
            return self._request("GET", path)
        if action == "list_issues":
            repo = params.get("repo")  # owner/name
            if not repo:
                return ConnectorResult(ok=False, error="repo required (owner/name)")
            return self._request("GET", f"/repos/{repo}/issues")
        if action == "create_issue":
            if not params.get("approved"):
                return ConnectorResult(
                    ok=False,
                    needs_approval=True,
                    message="Creating a GitHub issue requires approval",
                    approval_payload={
                        "action": "create_issue",
                        "repo": params.get("repo"),
                        "title": params.get("title"),
                        "body": params.get("body"),
                    },
                )
            repo = params.get("repo")
            title = params.get("title")
            if not repo or not title:
                return ConnectorResult(ok=False, error="repo and title required")
            return self._request(
                "POST",
                f"/repos/{repo}/issues",
                body={"title": title, "body": params.get("body") or ""},
            )
        return ConnectorResult(ok=False, error=f"Unknown action: {action}")

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> ConnectorResult:
        if not self._token:
            return ConnectorResult(ok=False, error="Not authenticated")
        # dry-run without network when token is "test" / "dry-run"
        if self._token in ("test", "dry-run", "demo"):
            return ConnectorResult(
                ok=True,
                message=f"[dry-run] {method} {path}",
                data={"dry_run": True, "method": method, "path": path, "body": body},
            )
        url = self._api + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "PEAR-Connector/0.90",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return ConnectorResult(ok=True, data=payload)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            return ConnectorResult(ok=False, error=f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return ConnectorResult(ok=False, error=str(e))
