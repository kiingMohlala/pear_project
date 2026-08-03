"""Centralized configuration profiles (v2.40)."""

from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULTS: Dict[str, Any] = {
    "profile": "development",
    "host": "0.0.0.0",
    "port": 8080,
    "data_dir": str(Path.home() / ".pear"),
    "log_level": "INFO",
    "log_json": False,
    "rate_limit_per_minute": 120,
    "rate_limit_burst": 30,
    "max_request_bytes": 1_000_000,
    "worker_heartbeat_timeout_s": 30,
    "worker_quarantine_failures": 5,
    "job_max_retries": 3,
    "backup_dir": str(Path.home() / ".pear" / "backups"),
    "audit_enabled": True,
    "metrics_enabled": True,
    "planner_use_learned_bias": False,
    "cors_origins": ["*"],
}

PROFILES: Dict[str, Dict[str, Any]] = {
    "development": {
        "log_level": "DEBUG",
        "log_json": False,
        "rate_limit_per_minute": 600,
        "audit_enabled": True,
    },
    "testing": {
        "log_level": "WARNING",
        "log_json": True,
        "rate_limit_per_minute": 10_000,
        "data_dir": str(Path("/tmp") / "pear_test"),
        "backup_dir": str(Path("/tmp") / "pear_test_backups"),
    },
    "production": {
        "log_level": "INFO",
        "log_json": True,
        "rate_limit_per_minute": 60,
        "rate_limit_burst": 15,
        "audit_enabled": True,
        "metrics_enabled": True,
    "planner_use_learned_bias": False,
        "cors_origins": [],
    },
}


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, profile: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None):
        self._lock = threading.RLock()
        self._path: Optional[Path] = None
        self._data: Dict[str, Any] = deepcopy(DEFAULTS)
        name = profile or os.environ.get("PEAR_PROFILE", "development")
        self.apply_profile(name)
        env_overrides = self._from_env()
        self._data.update(env_overrides)
        if overrides:
            self._data.update(overrides)
        self.validate()

    def _from_env(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        mapping = {
            "PEAR_HOST": ("host", str),
            "PEAR_PORT": ("port", int),
            "PEAR_DATA": ("data_dir", str),
            "PEAR_LOG_LEVEL": ("log_level", str),
            "PEAR_RATE_LIMIT": ("rate_limit_per_minute", int),
        }
        for env, (key, typ) in mapping.items():
            if env in os.environ:
                try:
                    out[key] = typ(os.environ[env])
                except Exception:
                    pass
        return out

    def apply_profile(self, name: str) -> None:
        if name not in PROFILES:
            raise ConfigError(f"Unknown profile: {name}")
        with self._lock:
            self._data = deepcopy(DEFAULTS)
            self._data.update(PROFILES[name])
            self._data["profile"] = name

    def validate(self) -> None:
        d = self._data
        if d["port"] < 1 or d["port"] > 65535:
            raise ConfigError("port out of range")
        if d["rate_limit_per_minute"] < 1:
            raise ConfigError("rate_limit_per_minute must be >= 1")
        if d["log_level"] not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ConfigError("invalid log_level")
        if d["profile"] not in PROFILES:
            raise ConfigError("invalid profile")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def as_dict(self) -> Dict[str, Any]:
        return deepcopy(self._data)

    def update(self, **kwargs) -> None:
        with self._lock:
            self._data.update(kwargs)
            self.validate()

    def load_file(self, path: Path) -> None:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._data.update(data)
            self._path = path
            self.validate()

    def reload(self) -> bool:
        """Hot-reload from file if configured. Returns True if reloaded."""
        if not self._path or not self._path.exists():
            return False
        mtime = self._path.stat().st_mtime
        if getattr(self, "_mtime", None) == mtime:
            return False
        self.load_file(self._path)
        self._mtime = mtime
        return True

    def save(self, path: Optional[Path] = None) -> Path:
        path = Path(path or self._path or Path(self._data["data_dir"]) / "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        self._path = path
        return path


_config: Optional[Config] = None
_config_lock = threading.Lock()


def get_config() -> Config:
    global _config
    with _config_lock:
        if _config is None:
            _config = Config()
        return _config


def set_config(cfg: Config) -> None:
    global _config
    with _config_lock:
        _config = cfg
