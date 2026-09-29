"""Holds one RateLimiter per route prefix.

The gateway creates one LimiterStore on startup. For each incoming request,
it looks up the limiter for the matched route and checks against a key
derived from the client (IP address, typically).

Routes without a limit policy are unlimited and don't appear here.
"""

import threading

from app.ratelimit import Decision, RateLimiter
from app.routes import RouteTable


class LimiterStore:
    def __init__(self, route_table: RouteTable):
        self._lock = threading.Lock()
        self._limiters: dict[str, RateLimiter] = {}
        for route in route_table.all():
            if route.limit is not None:
                self._limiters[route.prefix] = RateLimiter(route.limit)

    def check(self, route_prefix: str, client_key: str) -> Decision | None:
        """Check the rate limit for a route.

        Returns None if the route has no limit policy (unlimited).
        """
        with self._lock:
            limiter = self._limiters.get(route_prefix)
        if limiter is None:
            return None
        return limiter.check(client_key)

    def limiter_for(self, route_prefix: str) -> RateLimiter | None:
        with self._lock:
            return self._limiters.get(route_prefix)

    def reset(self, route_prefix: str | None = None) -> None:
        with self._lock:
            limiters = list(self._limiters.values()) if route_prefix is None else [
                self._limiters.get(route_prefix)
            ]
        for limiter in limiters:
            if limiter is not None:
                limiter.reset()

    def route_count(self) -> int:
        with self._lock:
            return len(self._limiters)