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


# ── PEAR 3.1 Gate 7: atomic persistence + honest corruption handling ────
#
# Several stores (auth users/sessions, connector credentials, goals,
# workflows) wrote their state with a plain path.write_text()/write_bytes()
# — which truncates the file before writing the new content. A crash or
# kill mid-write leaves a half-written, corrupted file. Worse, every one
# of those stores' load path wrapped the read in a bare `except Exception:
# pass` (or `self._data = {}`), which can't tell "file doesn't exist yet"
# (normal, first run) apart from "file exists but is corrupted" (a real
# incident) — both were handled by silently proceeding as if the store
# were empty. For the auth user database specifically, that's a silent,
# total lockout: every account just vanishes with no error anywhere.
#
# atomic_write_bytes/atomic_write_text: write to a temp file in the same
# directory, then os.replace() — atomic on POSIX and Windows, so the
# final file is always either fully the old version or fully the new one,
# never a partial write.
#
# safe_load: distinguishes "no file" from "file exists but unreadable",
# and for the latter preserves a timestamped .corrupted-<ts> backup next
# to it before the caller falls back to empty/default state, so there's
# at least a chance at forensic recovery instead of the bytes just being
# gone.

def atomic_write_bytes(path: "Path", data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def atomic_write_text(path: "Path", text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


# ── PEAR 3.1 Gate 12: locked read-modify-write for shared JSON stores ──
#
# atomic_write_bytes (above) prevents a *corrupted* file from a crash
# mid-write. It does nothing for a *lost* write: several stores (the
# Quant connector's research_memory.json/hypotheses.json/review_board.json
# in particular) are constructed fresh per user session, each loading the
# whole file into a private in-memory dict once at construction, then
# overwriting the whole file with only what that one instance knows about.
# Re-Audit 3 reproduced this directly: 30 concurrent callers each added
# one experiment to the shared corpus, and only 2 of 30 survived on disk
# — the rest were silently clobbered by whichever writer saved last, with
# no exception or log entry anywhere.
#
# locked_json_store serializes the full read-modify-write cycle for a
# given path: a threading.Lock keyed by the resolved path (covers the
# in-process multi-threaded case this app actually runs under —
# ThreadingHTTPServer / uvicorn single-worker), plus a best-effort
# fcntl.flock on a sibling `.lock` file for defense-in-depth if this ever
# runs multi-process. Callers must reload from disk *inside* the `with`
# block before mutating, so every writer starts from the latest committed
# state rather than a stale snapshot taken at construction time.
import threading as _threading
from contextlib import contextmanager as _contextmanager

_STORE_LOCKS: Dict[str, "_threading.Lock"] = {}
_STORE_LOCKS_GUARD = _threading.Lock()
_HELD_BY_THIS_THREAD = _threading.local()


def _store_lock_for(path: "Path") -> "_threading.Lock":
    key = str(Path(path).resolve())
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = _threading.Lock()
            _STORE_LOCKS[key] = lock
        return lock


@_contextmanager
def locked_json_store(path: "Path"):
    """
    Hold this for an entire load-mutate-save cycle against `path`.

    Reentrant *within the same thread*: some callers (e.g.
    HypothesisEngine.evaluate_candidate_through_pipeline calling its own
    spawn_candidate) legitimately nest calls against the same persist
    path on one call stack. A plain lock — thread or fcntl — would
    self-deadlock on the second, inner acquisition, so a thread that
    already holds this path's lock just re-enters without blocking.
    Cross-thread and cross-process callers still fully serialize.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    held = getattr(_HELD_BY_THIS_THREAD, "paths", None)
    if held is None:
        held = set()
        _HELD_BY_THIS_THREAD.paths = held

    if key in held:
        # Already holding this path's lock further up this same
        # thread's call stack — re-entering, not a new acquisition.
        yield
        return

    lock_path = path.parent / f".{path.name}.lock"
    thread_lock = _store_lock_for(path)
    with thread_lock:
        held.add(key)
        lf = None
        try:
            lf = open(lock_path, "a+")
            try:
                import fcntl
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass  # non-POSIX, or fcntl unavailable — thread lock still holds
            yield
        finally:
            held.discard(key)
            if lf is not None:
                try:
                    import fcntl
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                lf.close()


def safe_load_text(path: "Path", *, on_corrupt_label: str = "store") -> Optional[str]:
    """
    Returns the file's text, or None if it doesn't exist (normal — caller
    should proceed with default/empty state, no warning needed). If the
    file exists but can't be read, backs it up to a .corrupted-<ts>
    sibling, prints a loud warning (this is a real incident, not a
    first-run case), and returns None so the caller still degrades
    gracefully rather than crashing the whole service.
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        quarantine_corrupt_file(path, on_corrupt_label, e)
        return None


def safe_load_bytes(path: "Path", *, on_corrupt_label: str = "store") -> Optional[bytes]:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception as e:
        quarantine_corrupt_file(path, on_corrupt_label, e)
        return None


def quarantine_corrupt_file(path: "Path", label: str, error: Exception) -> None:
    import sys
    import time as _time
    backup = path.with_name(f"{path.name}.corrupted-{int(_time.time())}")
    try:
        import shutil
        shutil.copy2(path, backup)
        note = f"backed up to {backup}"
    except Exception:
        note = "backup ALSO failed — original file left in place, untouched"
    print(
        f"[PEAR] WARNING: {label} at {path} exists but could not be read ({error}). "
        f"Proceeding with empty/default state; {note}. This needs manual investigation — "
        f"data loss is possible.",
        file=sys.stderr,
    )


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
