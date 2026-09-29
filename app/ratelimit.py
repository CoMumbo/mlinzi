"""Token bucket rate limiter.

Each key (client IP, route, or IP+route) gets its own bucket. Buckets refill
at a constant rate up to a maximum capacity. Each request consumes one
token; requests with no tokens available are rejected.

This is the standard algorithm used by AWS, Cloudflare, and Stripe. It
allows short bursts up to `capacity` while enforcing a steady average rate
of `refill_per_second`.
"""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    capacity: float
    refill_per_second: float
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            self.last_refill = now

    def try_consume(self, amount: float = 1.0) -> bool:
        self.refill()
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def seconds_until_available(self, amount: float = 1.0) -> float:
        """How long until `amount` tokens are available."""
        self.refill()
        if self.tokens >= amount:
            return 0.0
        needed = amount - self.tokens
        return needed / self.refill_per_second

    def snapshot(self) -> dict:
        self.refill()
        return {
            "capacity": self.capacity,
            "refill_per_second": self.refill_per_second,
            "tokens": round(self.tokens, 2),
        }


@dataclass(frozen=True)
class Limit:
    """A rate limit policy."""
    capacity: float
    refill_per_second: float

    def __str__(self) -> str:
        # e.g. "10 burst, 2/s sustained"
        return f"{self.capacity:g} burst, {self.refill_per_second:g}/s"


@dataclass
class Decision:
    allowed: bool
    remaining: int
    limit: int
    retry_after_seconds: float = 0.0


class RateLimiter:
    """Registry of buckets, keyed by an opaque string.

    Thread-safe. Each unique key gets its own bucket on first use.
    """

    def __init__(self, limit: Limit):
        self.limit = limit
        self._lock = threading.Lock()
        self._buckets: dict[str, Bucket] = {}

    def _bucket_for(self, key: str) -> Bucket:
        b = self._buckets.get(key)
        if b is None:
            b = Bucket(
                capacity=self.limit.capacity,
                refill_per_second=self.limit.refill_per_second,
                tokens=self.limit.capacity,
            )
            self._buckets[key] = b
        return b

    def check(self, key: str, cost: float = 1.0) -> Decision:
        with self._lock:
            bucket = self._bucket_for(key)
            allowed = bucket.try_consume(cost)
            remaining = int(bucket.tokens)
            retry_after = 0.0 if allowed else bucket.seconds_until_available(cost)

        return Decision(
            allowed=allowed,
            remaining=remaining,
            limit=int(self.limit.capacity),
            retry_after_seconds=retry_after,
        )

    def peek(self, key: str) -> dict | None:
        with self._lock:
            b = self._buckets.get(key)
            return b.snapshot() if b else None

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def key_count(self) -> int:
        with self._lock:
            return len(self._buckets)