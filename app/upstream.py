"""Upstream instances and pools.

A Route points at an UpstreamPool, which contains one or more Upstream
instances. The pool round-robins across healthy instances and can be
marked/unmarked healthy by the health checker.

Each Upstream also has its own CircuitBreaker that reacts to actual
request outcomes (not just health pings). An upstream is eligible for
traffic when it is BOTH healthy and its breaker is not OPEN.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.circuit import BreakerState, CircuitBreaker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Upstream:
    url: str
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    healthy: bool = True
    last_checked_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0

    def mark_healthy(self) -> None:
        self.healthy = True
        self.last_checked_at = _utcnow()
        self.last_ok_at = self.last_checked_at
        self.last_error = None
        self.consecutive_failures = 0

    def mark_unhealthy(self, error: str) -> None:
        self.healthy = False
        self.last_checked_at = _utcnow()
        self.last_error = error
        self.consecutive_failures += 1

    def eligible(self) -> bool:
        """Can this upstream receive traffic right now?"""
        if not self.healthy:
            return False
        return self.breaker.state() != BreakerState.OPEN

    def snapshot(self) -> dict:
        return {
            "url": self.url,
            "healthy": self.healthy,
            "eligible": self.eligible(),
            "consecutive_failures": self.consecutive_failures,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
            "last_error": self.last_error,
            "breaker": self.breaker.snapshot(),
        }


class UpstreamPool:
    """A round-robin pool over a set of upstream instances.

    Thread-safe. Requests pick the next eligible instance in rotation.
    If no instance is eligible, `pick()` returns None.
    """

    def __init__(self, urls: list[str]):
        if not urls:
            raise ValueError("UpstreamPool requires at least one URL")
        self._lock = threading.Lock()
        self._upstreams: list[Upstream] = [Upstream(url=u) for u in urls]
        self._next_index = 0

    def pick(self) -> Upstream | None:
        """Return the next eligible upstream in rotation, or None if none are eligible."""
        with self._lock:
            n = len(self._upstreams)
            for _ in range(n):
                u = self._upstreams[self._next_index]
                self._next_index = (self._next_index + 1) % n
                if u.eligible():
                    return u
        return None

    def upstreams(self) -> list[Upstream]:
        with self._lock:
            return list(self._upstreams)

    def healthy_count(self) -> int:
        with self._lock:
            return sum(1 for u in self._upstreams if u.healthy)

    def eligible_count(self) -> int:
        with self._lock:
            return sum(1 for u in self._upstreams if u.eligible())

    def total_count(self) -> int:
        return len(self._upstreams)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [u.snapshot() for u in self._upstreams]