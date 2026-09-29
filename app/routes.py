"""Route table: maps path prefixes to upstream pools.

A gateway's job starts here. Given an incoming request path, find the
longest matching prefix and hand the request to one of that route's
upstream instances.
"""

from dataclasses import dataclass

from app.ratelimit import Limit


@dataclass(frozen=True)
class Route:
    prefix: str
    upstreams: tuple[str, ...]      # base URLs, e.g. ("http://127.0.0.1:9001",)
    strip_prefix: bool = False
    limit: Limit | None = None      # rate limit policy, or None for unlimited

    def matches(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")


class RouteTable:
    """Ordered set of routes. Matching prefers the longest prefix."""

    def __init__(self, routes: list[Route] | None = None):
        self._routes: list[Route] = []
        for r in routes or []:
            self.add(r)

    def add(self, route: Route) -> None:
        for existing in self._routes:
            if existing.prefix == route.prefix:
                raise ValueError(f"duplicate route prefix: {route.prefix}")
        self._routes.append(route)
        self._routes.sort(key=lambda r: len(r.prefix), reverse=True)

    def match(self, path: str) -> Route | None:
        for route in self._routes:
            if route.matches(path):
                return route
        return None

    def all(self) -> list[Route]:
        return list(self._routes)

    def __len__(self) -> int:
        return len(self._routes)


def build_route_table() -> RouteTable:
    """Build the default route table for this gateway.

    Limits are intentionally small so the demo and tests hit them quickly.
    """
    from app.config import Config

    echo_base = f"http://127.0.0.1:{Config.ECHO_PORT}"

    return RouteTable([
        Route(
            prefix="/echo",
            upstreams=(echo_base,),
            strip_prefix=False,
            limit=Limit(capacity=10, refill_per_second=2.0),
        ),
        Route(
            prefix="/api",
            upstreams=(echo_base,),
            strip_prefix=True,
            limit=Limit(capacity=5, refill_per_second=1.0),
        ),
    ])