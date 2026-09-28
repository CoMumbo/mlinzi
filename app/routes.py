"""Route table: maps path prefixes to upstream services.

A gateway's job starts here. Given an incoming request path, find the
longest matching prefix and hand the request to that upstream.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    prefix: str
    upstream: str           # base URL of the upstream, e.g. "http://127.0.0.1:9001"
    strip_prefix: bool = False  # if True, remove `prefix` before forwarding

    def matches(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")


class RouteTable:
    """Ordered set of routes. Matching prefers the longest prefix."""

    def __init__(self, routes: list[Route] | None = None):
        self._routes: list[Route] = []
        for r in routes or []:
            self.add(r)

    def add(self, route: Route) -> None:
        # Reject duplicates so a typo doesn't silently shadow a real route
        for existing in self._routes:
            if existing.prefix == route.prefix:
                raise ValueError(f"duplicate route prefix: {route.prefix}")
        self._routes.append(route)
        # Longest prefix first so more specific routes win
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

    The bundled echo upstream is the default target so the demo and tests
    work out of the box. Real deployments would load routes from config.
    """
    from app.config import Config

    echo_base = f"http://127.0.0.1:{Config.ECHO_PORT}"

    return RouteTable([
        Route(prefix="/echo", upstream=echo_base, strip_prefix=False),
        Route(prefix="/api", upstream=echo_base, strip_prefix=True),
    ])