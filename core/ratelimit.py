"""Token-bucket rate limiting per user / API client (v2.40)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class Bucket:
    tokens: float
    last: float
    rate: float  # tokens per second
    burst: float


class RateLimiter:
    def __init__(self, per_minute: int = 60, burst: int = 15):
        self.per_minute = per_minute
        self.burst = burst
        self._buckets: Dict[str, Bucket] = {}
        self._lock = threading.Lock()

    def configure(self, per_minute: int, burst: Optional[int] = None) -> None:
        self.per_minute = per_minute
        if burst is not None:
            self.burst = burst

    def allow(self, key: str) -> Tuple[bool, Dict[str, float]]:
        now = time.time()
        rate = self.per_minute / 60.0
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = Bucket(tokens=float(self.burst), last=now, rate=rate, burst=float(self.burst))
                self._buckets[key] = b
            # refill
            elapsed = max(0.0, now - b.last)
            b.tokens = min(b.burst, b.tokens + elapsed * b.rate)
            b.last = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, {"remaining": b.tokens, "limit": self.per_minute}
            return False, {"remaining": b.tokens, "limit": self.per_minute, "retry_after_s": (1.0 - b.tokens) / max(b.rate, 1e-6)}
