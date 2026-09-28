import time

from app.middleware import (
    RequestContext,
    attach_request_id,
    start_timer,
    stop_timer,
    log_request,
    record_stats,
    run_before,
    run_after,
)
from app.stats import STATS


def test_attach_request_id_sets_header():
    ctx = RequestContext(method="GET", path="/x")
    attach_request_id(ctx)
    assert ctx.request_id
    assert len(ctx.request_id) == 16
    assert ctx.response_headers["X-Request-ID"] == ctx.request_id


def test_attach_request_id_is_unique():
    a = RequestContext(method="GET", path="/x")
    b = RequestContext(method="GET", path="/x")
    attach_request_id(a)
    attach_request_id(b)
    assert a.request_id != b.request_id


def test_start_and_stop_timer():
    ctx = RequestContext(method="GET", path="/x")
    start_timer(ctx)
    time.sleep(0.02)
    stop_timer(ctx)

    assert ctx.duration_ms >= 15  # at least the sleep, with slack
    assert "X-Gateway-Time-Ms" in ctx.response_headers
    # header value should match the recorded duration (formatted)
    assert ctx.response_headers["X-Gateway-Time-Ms"].startswith(f"{ctx.duration_ms:.2f}"[:4])


def test_stop_timer_without_start_is_safe():
    ctx = RequestContext(method="GET", path="/x")
    stop_timer(ctx)  # should not crash
    assert ctx.duration_ms == 0.0


def test_record_stats_skips_when_no_status():
    STATS.reset()
    ctx = RequestContext(method="GET", path="/x")
    record_stats(ctx)  # no status_code set
    assert STATS.snapshot()["total_requests"] == 0


def test_record_stats_records_when_status_present():
    STATS.reset()
    ctx = RequestContext(method="GET", path="/x")
    ctx.status_code = 200
    ctx.route_prefix = "/x"
    ctx.duration_ms = 3.5
    record_stats(ctx)

    s = STATS.snapshot()
    assert s["total_requests"] == 1
    assert s["requests_by_status"]["200"] == 1
    assert s["requests_by_route"]["/x"] == 1


def test_run_before_and_after_full_chain():
    STATS.reset()
    ctx = RequestContext(method="GET", path="/hello")
    run_before(ctx)

    assert ctx.request_id
    assert ctx.started_at > 0

    time.sleep(0.01)
    ctx.status_code = 200
    ctx.route_prefix = "/hello"
    run_after(ctx)

    assert ctx.duration_ms >= 5
    assert "X-Request-ID" in ctx.response_headers
    assert "X-Gateway-Time-Ms" in ctx.response_headers
    assert STATS.snapshot()["total_requests"] == 1