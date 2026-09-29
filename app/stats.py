"""Thread-safe in-memory stats collector for the gateway."""

import threading
import time
from collections import defaultdict, deque


class Stats:
    def __init__(self, latency_window: int = 1000):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._total = 0
        self._rejected = 0
        self._by_status: dict[int, int] = defaultdict(int)
        self._by_route: dict[str, int] = defaultdict(int)
        self._latencies_ms: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=latency_window)
        )

    def record(self, route_prefix: str | None, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self._total += 1
            self._by_status[status_code] += 1
            key = route_prefix or "_unmatched"
            self._by_route[key] += 1
            self._latencies_ms[key].append(latency_ms)

    def record_rejected(self) -> None:
        """Increment the rate-limited request counter."""
        with self._lock:
            self._rejected += 1

    def snapshot(self) -> dict:
        with self._lock:
            total = self._total
            rejected = self._rejected
            by_status = dict(self._by_status)
            by_route = dict(self._by_route)
            latencies = {k: list(v) for k, v in self._latencies_ms.items()}
            uptime = time.time() - self._started_at

        all_samples = [x for samples in latencies.values() for x in samples]
        avg_latency = sum(all_samples) / len(all_samples) if all_samples else 0.0
        p95 = _percentile(all_samples, 95) if all_samples else 0.0
        p99 = _percentile(all_samples, 99) if all_samples else 0.0

        return {
            "uptime_seconds": round(uptime, 2),
            "total_requests": total,
            "rate_limited_requests": rejected,
            "requests_by_status": {str(k): v for k, v in sorted(by_status.items())},
            "requests_by_route": dict(sorted(by_route.items())),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95, 2),
            "p99_latency_ms": round(p99, 2),
        }

    def reset(self) -> None:
        with self._lock:
            self._total = 0
            self._rejected = 0
            self._by_status.clear()
            self._by_route.clear()
            self._latencies_ms.clear()
            self._started_at = time.time()


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


STATS = Stats()