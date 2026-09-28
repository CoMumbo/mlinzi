"""Request middleware: request IDs, timing, logging, stats.

Design: a middleware is a function that takes a request context and returns
nothing; it mutates the context. The gateway runs a list of these before
and after forwarding, in order.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field

from app.stats import STATS

log = logging.getLogger("mlinzi.middleware")


@dataclass
class RequestContext:
    """Everything we know about a request as it flows through the gateway."""
    method: str
    path: str
    request_id: str = ""
    route_prefix: str | None = None
    status_code: int | None = None
    started_at: float = 0.0
    duration_ms: float = 0.0
    response_headers: dict = field(default_factory=dict)


# --- Middleware ------------------------------------------------------------

def attach_request_id(ctx: RequestContext) -> None:
    """Generate a request ID. Clients can supply one via X-Request-ID."""
    ctx.request_id = uuid.uuid4().hex[:16]
    ctx.response_headers["X-Request-ID"] = ctx.request_id


def start_timer(ctx: RequestContext) -> None:
    ctx.started_at = time.perf_counter()


def stop_timer(ctx: RequestContext) -> None:
    if ctx.started_at:
        ctx.duration_ms = (time.perf_counter() - ctx.started_at) * 1000.0
    ctx.response_headers["X-Gateway-Time-Ms"] = f"{ctx.duration_ms:.2f}"


def log_request(ctx: RequestContext) -> None:
    log.info(
        "%s %s -> %s %s (%.2fms) req_id=%s",
        ctx.method,
        ctx.path,
        ctx.status_code,
        ctx.route_prefix or "no-route",
        ctx.duration_ms,
        ctx.request_id,
    )


def record_stats(ctx: RequestContext) -> None:
    if ctx.status_code is None:
        return
    STATS.record(ctx.route_prefix, ctx.status_code, ctx.duration_ms)


# --- Chains ----------------------------------------------------------------

BEFORE = [attach_request_id, start_timer]
AFTER = [stop_timer, log_request, record_stats]


def run_before(ctx: RequestContext) -> None:
    for fn in BEFORE:
        fn(ctx)


def run_after(ctx: RequestContext) -> None:
    for fn in AFTER:
        fn(ctx)