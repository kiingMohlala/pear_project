"""Input validation, upload guards, and crypto helpers (v3.1)."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Request limits
DEFAULT_MAX_BODY = 1_000_000  # 1 MB
MAX_UPLOAD_BYTES = 10_000_000  # 10 MB
ALLOWED_UPLOAD_EXT = {".pdf", ".docx", ".txt", ".csv", ".xlsx", ".png", ".jpg", ".jpeg", ".md"}
ALLOWED_UPLOAD_MIME_PREFIXES = ("application/pdf", "text/", "image/", "application/vnd", "application/json")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_string(value: Any, max_len: int = 10_000) -> str:
    if value is None:
        return ""
    s = str(value)
    s = _CONTROL_CHARS.sub("", s)
    if len(s) > max_len:
        s = s[:max_len]
    return s


def sanitize_object(data: Any, max_str: int = 10_000, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(data, str):
        return sanitize_string(data, max_str)
    if isinstance(data, dict):
        return {sanitize_string(k, 256): sanitize_object(v, max_str, depth + 1) for k, v in list(data.items())[:200]}
    if isinstance(data, list):
        return [sanitize_object(v, max_str, depth + 1) for v in data[:500]]
    if isinstance(data, (int, float, bool)) or data is None:
        return data
    return sanitize_string(data, max_str)


def validate_body_size(body: bytes, max_bytes: int = DEFAULT_MAX_BODY) -> None:
    if body is not None and len(body) > max_bytes:
        raise ValueError(f"request body exceeds {max_bytes} bytes")


def safe_upload_path(workspace: Path, filename: str) -> Path:
    name = Path(filename or "upload.bin").name
    # strip path traversal
    name = name.replace("..", "").replace("/", "").replace("\\", "")
    if not name:
        name = "upload.bin"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise ValueError(f"file type not allowed: {ext or '(none)'}")
    workspace = Path(workspace).resolve()
    dest = (workspace / name).resolve()
    if not str(dest).startswith(str(workspace)):
        raise ValueError("invalid upload path")
    return dest


def check_upload_bytes(data: bytes, filename: str = "") -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    if len(data) == 0:
        raise ValueError("empty upload")
    ext = Path(filename or "").suffix.lower()
    if ext and ext not in ALLOWED_UPLOAD_EXT:
        raise ValueError(f"file type not allowed: {ext}")


# ── credential encryption ─────────────────────────────────────────

class SecretBox:
    """
    Fernet-like XOR stream with key derived from master secret.
    Prefer cryptography.fernet when available; fallback is deterministic offline.
    """

    def __init__(self, master_key: Optional[str] = None):
        env = master_key or os.environ.get("PEAR_SECRET_KEY") or "pear-dev-secret-change-me"
        self._key = hashlib.sha256(env.encode("utf-8")).digest()
        self._fernet = None
        try:
            from cryptography.fernet import Fernet
            import base64
            fkey = base64.urlsafe_b64encode(self._key)
            self._fernet = Fernet(fkey)
        except Exception:
            pass

    def encrypt(self, plaintext: str) -> str:
        raw = plaintext.encode("utf-8")
        if self._fernet:
            return "f:" + self._fernet.encrypt(raw).decode("utf-8")
        # fallback: nonce + xor + hmac
        nonce = secrets.token_bytes(16)
        stream = hashlib.sha256(self._key + nonce).digest()
        out = bytes(b ^ stream[i % len(stream)] for i, b in enumerate(raw))
        tag = hmac.new(self._key, nonce + out, hashlib.sha256).hexdigest()[:32]
        return "x:" + nonce.hex() + ":" + out.hex() + ":" + tag

    def decrypt(self, token: str) -> str:
        if token.startswith("f:") and self._fernet:
            return self._fernet.decrypt(token[2:].encode("utf-8")).decode("utf-8")
        if token.startswith("x:"):
            _, nonce_h, data_h, tag = token.split(":", 3)
            nonce = bytes.fromhex(nonce_h)
            data = bytes.fromhex(data_h)
            expect = hmac.new(self._key, nonce + data, hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(expect, tag):
                raise ValueError("integrity check failed")
            stream = hashlib.sha256(self._key + nonce).digest()
            raw = bytes(b ^ stream[i % len(stream)] for i, b in enumerate(data))
            return raw.decode("utf-8")
        # plaintext legacy
        return token

    def rotate(self, token: str, new_box: "SecretBox") -> str:
        return new_box.encrypt(self.decrypt(token))


# ── CORS ──────────────────────────────────────────────────────────

DEFAULT_CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
DEFAULT_CORS_HEADERS = "Authorization, Content-Type, X-Requested-With, Accept, Origin"
DEFAULT_CORS_MAX_AGE = "600"


def cors_allowed_origins() -> list:
    """Origins from config; empty list means deny browser cross-origin (except same-origin)."""
    try:
        from .config import get_config
        origins = get_config().get("cors_origins")
        if origins is None:
            return ["*"]
        if isinstance(origins, str):
            origins = [o.strip() for o in origins.split(",") if o.strip()]
        return list(origins)
    except Exception:
        return ["*"]


def cors_origin_header(request_origin: str = "") -> tuple:
    """
    Return (allow_origin_value, vary_origin: bool).
    - ["*"] → allow any (no credentials)
    - explicit list → echo matching Origin or empty if no match
    - [] → omit ACAO (browser blocks cross-origin)
    """
    allowed = cors_allowed_origins()
    if allowed == ["*"] or "*" in allowed:
        return "*", False
    if not allowed:
        return "", False
    origin = (request_origin or "").strip()
    if origin and origin in allowed:
        return origin, True
    return "", False


def apply_cors_headers(send_header, request_origin: str = "") -> None:
    """send_header is callable(name, value) like BaseHTTPRequestHandler.send_header."""
    value, vary = cors_origin_header(request_origin)
    if value:
        send_header("Access-Control-Allow-Origin", value)
        if vary:
            send_header("Vary", "Origin")
        send_header("Access-Control-Allow-Methods", DEFAULT_CORS_METHODS)
        send_header("Access-Control-Allow-Headers", DEFAULT_CORS_HEADERS)
        send_header("Access-Control-Max-Age", DEFAULT_CORS_MAX_AGE)
