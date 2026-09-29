"""Background health checker.

Periodically pings each upstream's health endpoint. Marks instances
healthy or unhealthy on the pool. Runs on its own thread so requests
are never blocked by a slow upstream.
"""

import logging
import threading
import time

import requests

from app.upstream import UpstreamPool

log = logging.getLogger("mlinzi.health")


class HealthChecker:
    """Pings every upstream in a pool on a fixed interval."""

    def __init__(
        self,
        pool: UpstreamPool,
        interval: float = 5.0,
        timeout: float = 2.0,
        health_path: str = "/gateway/health",
    ):
        self.pool = pool
        self.interval = interval
        self.timeout = timeout
        self.health_path = health_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="health-checker", daemon=True)
        self._thread.start()
        log.info("health checker started (interval=%.1fs, timeout=%.1fs)",
                 self.interval, self.timeout)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            log.info("health checker stopped")

    def check_once(self) -> None:
        """Run a single health pass over all upstreams. Used by tests."""
        for upstream in self.pool.upstreams():
            self._check_one(upstream)

    def _check_one(self, upstream) -> None:
        url = upstream.url.rstrip("/") + self.health_path
        try:
            r = requests.get(url, timeout=self.timeout)
            if 200 <= r.status_code < 300:
                was_unhealthy = not upstream.healthy
                upstream.mark_healthy()
                if was_unhealthy:
                    log.info("upstream recovered: %s", upstream.url)
            else:
                self._mark_failed(upstream, f"health returned {r.status_code}")
        except requests.RequestException as e:
            self._mark_failed(upstream, f"{type(e).__name__}: {e}")

    def _mark_failed(self, upstream, error: str) -> None:
        was_healthy = upstream.healthy
        upstream.mark_unhealthy(error)
        if was_healthy:
            log.warning("upstream unhealthy: %s (%s)", upstream.url, error)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:
                log.exception("health check pass failed")
            self._stop.wait(self.interval)