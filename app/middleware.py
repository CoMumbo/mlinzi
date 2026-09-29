"""Request middleware: request IDs, timing, logging, stats, rate limits."""

import logging
import time
import uuid
from dataclasses import dataclass, field

from app.ratelimit import Decision
from app.stats import STATS

log = logging.getLogger("mlinzi.middleware")


@dataclass
class RequestContext:
    method: str
    path: str
    client_ip: str = ""
    request_id: str = ""
    route_prefix: str | None = None
    status_code: int | None = None
    started_at: float = 0.0
    duration_ms: float = 0.0
    rate_decision: Decision | None = None
    response_headers: dict = field(default_factory=dict)


# --- Middleware ------------------------------------------------------------

def attach_request_id(ctx: RequestContext) -> None:
    ctx.request_id = uuid.uuid4().hex[:16]
    ctx.response_headers["X-Request-ID"] = ctx.request_id


def start_timer(ctx: RequestContext) -> None:
    ctx.started_at = time.perf_counter()


def stop_timer(ctx: RequestContext) -> None:
    if ctx.started_at:
        ctx.duration_ms = (time.perf_counter() - ctx.started_at) * 1000.0
    ctx.response_headers["X-Gateway-Time-Ms"] = f"{ctx.duration_ms:.2f}"


def attach_ratelimit_headers(ctx: RequestContext) -> None:
    """Emit X-RateLimit-* headers based on the decision (if any)."""
    if ctx.rate_decision is None:
        return
    d = ctx.rate_decision
    ctx.response_headers["X-RateLimit-Limit"] = str(d.limit)
    ctx.response_headers["X-RateLimit-Remaining"] = str(max(d.remaining, 0))
    if not d.allowed:
        retry = max(1, int(round(d.retry_after_seconds)))
        ctx.response_headers["Retry-After"] = str(retry)


def log_request(ctx: RequestContext) -> None:
    log.info(
        "%s %s -> %s %s (%.2fms) req_id=%s client=%s",
        ctx.method,
        ctx.path,
        ctx.status_code,
        ctx.route_prefix or "no-route",
        ctx.duration_ms,
        ctx.request_id,
        ctx.client_ip,
    )


def record_stats(ctx: RequestContext) -> None:
    if ctx.status_code is None:
        return
    STATS.record(ctx.route_prefix, ctx.status_code, ctx.duration_ms)
    if ctx.status_code == 429:
        STATS.record_rejected()


# --- Chains ----------------------------------------------------------------

BEFORE = [attach_request_id, start_timer]
AFTER = [stop_timer, attach_ratelimit_headers, log_request, record_stats]


def run_before(ctx: RequestContext) -> None:
    for fn in BEFORE:
        fn(ctx)


def run_after(ctx: RequestContext) -> None:
    for fn in AFTER:
        fn(ctx)