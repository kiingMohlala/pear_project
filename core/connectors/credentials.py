"""
Local encrypted credential store for connectors.
Uses Fernet when cryptography is available; otherwise XOR + key file (dev only).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


class CredentialStore:
    def __init__(self, path: Optional[Path] = None):
        base = Path.home() / ".pear"
        base.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else base / "credentials.enc"
        self.key_path = base / ".cred_key"
        self._key = self._load_or_create_key()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = hashlib.sha256(os.urandom(32)).digest()
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
            if self.path.exists():
                os.chmod(self.path, 0o600)
        except OSError:
            pass
        return key

    def _fernet(self):
        try:
            from cryptography.fernet import Fernet
            fkey = base64.urlsafe_b64encode(self._key)
            return Fernet(fkey)
        except Exception:
            return None

    def _encrypt(self, plain: bytes) -> bytes:
        f = self._fernet()
        if f:
            return f.encrypt(plain)
        # XOR stream (development fallback — not for high-security production)
        out = bytearray()
        for i, b in enumerate(plain):
            out.append(b ^ self._key[i % len(self._key)])
        return base64.urlsafe_b64encode(bytes(out))

    def _decrypt(self, blob: bytes) -> bytes:
        f = self._fernet()
        if f:
            try:
                return f.decrypt(blob)
            except Exception:
                pass
        try:
            raw = base64.urlsafe_b64decode(blob)
            out = bytearray()
            for i, b in enumerate(raw):
                out.append(b ^ self._key[i % len(self._key)])
            return bytes(out)
        except Exception as e:
            raise ValueError(f"Cannot decrypt credentials: {e}") from e

    def _load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            plain = self._decrypt(self.path.read_bytes())
            self._data = json.loads(plain.decode("utf-8"))
        except Exception:
            self._data = {}

    def _save(self) -> None:
        payload = json.dumps(self._data).encode("utf-8")
        self.path.write_bytes(self._encrypt(payload))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def set(self, connector: str, credentials: Dict[str, Any]) -> None:
        self._data[connector] = {
            "credentials": credentials,
            "updated_at": time.time(),
        }
        self._save()

    def get(self, connector: str) -> Optional[Dict[str, Any]]:
        entry = self._data.get(connector)
        if not entry:
            return None
        return dict(entry.get("credentials") or {})

    def delete(self, connector: str) -> None:
        self._data.pop(connector, None)
        self._save()

    def list_connectors(self) -> list:
        return sorted(self._data.keys())

    def rotate_key(self) -> Dict[str, Any]:
        """Generate new key and re-encrypt store (key rotation)."""
        old_key = self._key
        new_key = hashlib.sha256(os.urandom(32)).digest()
        # decrypt with old
        data = dict(self._data)
        self._key = new_key
        self.key_path.write_bytes(new_key)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._data = data
        self._save()
        return {"ok": True, "encryption": "fernet" if self._fernet() else "xor-dev"}

    def status(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "encryption": "fernet" if self._fernet() else "xor-dev",
            "connectors": {
                name: {"updated_at": e.get("updated_at"), "keys": list((e.get("credentials") or {}).keys())}
                for name, e in self._data.items()
            },
        }
