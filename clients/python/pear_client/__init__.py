"""Thin PEAR REST client (v3.1) — stable /v1 surface only."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PearClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method: str, path: str, body: Optional[dict] = None) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(f"{self.base_url}{path}", data=data, headers=self._headers(), method=method)
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            raw = e.read().decode("utf-8") if e.fp else str(e)
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": False, "error": raw, "status": e.code}
        except URLError as e:
            return {"ok": False, "error": str(e)}

    def login(self, username: str, password: str) -> Dict[str, Any]:
        out = self.request("POST", "/auth/login", {"username": username, "password": password})
        if out.get("token"):
            self.token = out["token"]
        return out

    def chat(self, message: str) -> Dict[str, Any]:
        return self.request("POST", "/v1/chat", {"message": message})

    def health(self) -> Dict[str, Any]:
        return self.request("GET", "/health")


__all__ = ["PearClient"]
