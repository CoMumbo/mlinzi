"""Holds one UpstreamPool per route, plus a health checker for each pool."""

import threading

from app.health import HealthChecker
from app.routes import RouteTable
from app.upstream import UpstreamPool


class PoolStore:
    def __init__(self, route_table: RouteTable, health_interval: float = 5.0, health_timeout: float = 2.0):
        self._pools: dict[str, UpstreamPool] = {}
        self._checkers: list[HealthChecker] = []

        for route in route_table.all():
            pool = UpstreamPool(list(route.upstreams))
            self._pools[route.prefix] = pool
            self._checkers.append(
                HealthChecker(pool, interval=health_interval, timeout=health_timeout)
            )

    def pool_for(self, route_prefix: str) -> UpstreamPool | None:
        return self._pools.get(route_prefix)

    def start_health_checks(self) -> None:
        for c in self._checkers:
            c.start()

    def stop_health_checks(self) -> None:
        for c in self._checkers:
            c.stop()

    def snapshot(self) -> dict[str, list[dict]]:
        return {prefix: pool.snapshot() for prefix, pool in self._pools.items()}