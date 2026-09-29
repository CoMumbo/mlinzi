import threading
import time

import pytest
from flask import Flask

from app.config import Config
from app.echo_upstream import create_echo_app
from app.gateway import create_gateway
from app.routes import Route, RouteTable
from app.stats import STATS


@pytest.fixture(scope="module")
def echo_upstream():
    """Start the echo upstream on a test port, yield the base URL, tear down."""
    port = 9101
    app = create_echo_app()
    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    time.sleep(0.5)
    yield f"http://127.0.0.1:{port}"


@pytest.fixture
def gateway_client(echo_upstream):
    STATS.reset()
    routes = RouteTable([
        Route("/echo", (echo_upstream,), strip_prefix=False),
        Route("/api", (echo_upstream,), strip_prefix=True),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health_endpoint(gateway_client):
    r = gateway_client.get("/gateway/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_routes_endpoint(gateway_client):
    r = gateway_client.get("/gateway/routes")
    assert r.status_code == 200
    body = r.get_json()
    assert any(x["prefix"] == "/echo" for x in body)
    assert any(x["prefix"] == "/api" for x in body)


def test_stats_endpoint_after_requests(gateway_client):
    gateway_client.get("/echo/hello")
    gateway_client.get("/api/users/1")
    r = gateway_client.get("/gateway/stats")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total_requests"] == 2
    assert body["requests_by_route"]["/echo"] == 1
    assert body["requests_by_route"]["/api"] == 1


def test_forward_with_prefix_preserved(gateway_client):
    r = gateway_client.get("/echo/hello")
    assert r.status_code == 200
    body = r.get_json()
    assert body["upstream"] == "echo"
    assert body["path"] == "/echo/hello"
    assert body["method"] == "GET"


def test_forward_with_prefix_stripped(gateway_client):
    r = gateway_client.get("/api/users/1")
    assert r.status_code == 200
    body = r.get_json()
    assert body["path"] == "/users/1"


def test_forward_preserves_query_string(gateway_client):
    r = gateway_client.get("/echo/search?q=hello&limit=10")
    body = r.get_json()
    assert body["query"] == "q=hello&limit=10"


def test_forward_post_body(gateway_client):
    r = gateway_client.post(
        "/api/items",
        json={"name": "widget"},
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["method"] == "POST"
    assert body["path"] == "/items"
    assert "widget" in body["body"]


def test_response_includes_request_id_and_timing(gateway_client):
    r = gateway_client.get("/echo/hello")
    assert "X-Request-ID" in r.headers
    assert "X-Gateway-Time-Ms" in r.headers
    assert len(r.headers["X-Request-ID"]) == 16


def test_unmatched_path_returns_404(gateway_client):
    r = gateway_client.get("/nonexistent")
    assert r.status_code == 404
    assert r.get_json()["error"] == "no route"


def test_unreachable_upstream_returns_502():
    """A route with a live-looking URL that nothing listens on returns 502."""
    STATS.reset()
    routes = RouteTable([
        Route("/dead", ("http://127.0.0.1:1",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/dead/anything")
    assert r.status_code == 502
    assert b"unreachable" in r.data


# --- Rate limiting ---------------------------------------------------------

def test_rate_limit_allows_burst_then_429():
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/limited", ("http://127.0.0.1:9101",), strip_prefix=False,
              limit=Limit(capacity=2, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        assert c.get("/limited/a").status_code == 200
        assert c.get("/limited/b").status_code == 200
        r = c.get("/limited/c")
        assert r.status_code == 429
        assert r.get_json()["error"] == "rate limit exceeded"


def test_rate_limit_headers_on_success():
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/rl", ("http://127.0.0.1:9101",), strip_prefix=False,
              limit=Limit(capacity=5, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        r = c.get("/rl/x")
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Limit"] == "5"
        assert r.headers["X-RateLimit-Remaining"] == "4"


def test_rate_limit_headers_on_429():
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/rl", ("http://127.0.0.1:9101",), strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        c.get("/rl/x")
        r = c.get("/rl/y")
        assert r.status_code == 429
        assert r.headers["X-RateLimit-Limit"] == "1"
        assert r.headers["X-RateLimit-Remaining"] == "0"
        assert "Retry-After" in r.headers


def test_rate_limit_is_per_route():
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/a", ("http://127.0.0.1:9101",), strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
        Route("/b", ("http://127.0.0.1:9101",), strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        assert c.get("/a/1").status_code == 200
        assert c.get("/a/2").status_code == 429
        assert c.get("/b/1").status_code == 200


def test_unlimited_route_has_no_ratelimit_headers():
    STATS.reset()
    routes = RouteTable([
        Route("/free", ("http://127.0.0.1:9101",), strip_prefix=False, limit=None),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        r = c.get("/free/x")
        assert r.status_code == 200
        assert "X-RateLimit-Limit" not in r.headers


def test_rate_limited_requests_counted_in_stats():
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/s", ("http://127.0.0.1:9101",), strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        c.get("/s/1")
        c.get("/s/2")
        c.get("/s/3")
        snap = c.get("/gateway/stats").get_json()
    assert snap["rate_limited_requests"] == 2
    assert snap["requests_by_status"]["429"] == 2


# --- Load balancing / 503 --------------------------------------------------

def test_no_healthy_upstream_returns_503():
    """A route with all upstreams marked unhealthy returns 503."""
    STATS.reset()
    routes = RouteTable([
        Route("/pool", ("http://127.0.0.1:9101",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    # Reach in and mark the only upstream unhealthy
    pools = app.extensions["pools"]
    pool = pools.pool_for("/pool")
    pool.upstreams()[0].mark_unhealthy("test")

    with app.test_client() as c:
        r = c.get("/pool/x")
        assert r.status_code == 503
        assert r.get_json()["error"] == "no healthy upstream"


def test_routes_endpoint_shows_upstreams(echo_upstream):
    STATS.reset()
    routes = RouteTable([
        Route("/multi", (echo_upstream,), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        r = c.get("/gateway/routes")
        body = r.get_json()
        multi = next(x for x in body if x["prefix"] == "/multi")
        assert len(multi["upstreams"]) == 1
        assert multi["upstreams"][0]["url"] == echo_upstream
        assert multi["upstreams"][0]["healthy"] is True
        # --- Circuit breaker integration -------------------------------------------

def test_breaker_trips_and_blocks_upstream():
    """Force the breaker open and confirm the pool routes around it."""
    STATS.reset()
    routes = RouteTable([
        Route("/cb", ("http://127.0.0.1:9101",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    pools = app.extensions["pools"]
    pool = pools.pool_for("/cb")
    upstream = pool.upstreams()[0]

    upstream.breaker.force_open()

    with app.test_client() as c:
        r = c.get("/cb/x")
        assert r.status_code == 503
        assert r.get_json()["error"] == "no healthy upstream"


def test_breaker_records_success_on_2xx():
    """A successful request through the gateway marks the breaker happy."""
    STATS.reset()
    routes = RouteTable([
        Route("/ok", ("http://127.0.0.1:9101",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    pools = app.extensions["pools"]
    upstream = pools.pool_for("/ok").upstreams()[0]

    with app.test_client() as c:
        r = c.get("/ok/x")
        assert r.status_code == 200

    snap = upstream.breaker.snapshot()
    assert snap["state"] == "closed"
    assert snap["window_samples"] == 1
    assert snap["window_failures"] == 0


def test_breaker_ignores_4xx():
    """4xx responses are the client's fault, not the upstream's."""
    STATS.reset()
    routes = RouteTable([
        Route("/404", ("http://127.0.0.1:9101",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    pools = app.extensions["pools"]
    upstream = pools.pool_for("/404").upstreams()[0]

    from app.gateway import _record_breaker_outcome
    _record_breaker_outcome(upstream, 404)

    snap = upstream.breaker.snapshot()
    assert snap["window_samples"] == 0
    assert snap["window_failures"] == 0


def test_breaker_records_502_as_failure():
    """A 502 (upstream unreachable) counts as a breaker failure."""
    STATS.reset()
    routes = RouteTable([
        Route("/dead", ("http://127.0.0.1:1",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    pools = app.extensions["pools"]
    upstream = pools.pool_for("/dead").upstreams()[0]

    with app.test_client() as c:
        r = c.get("/dead/x")
        assert r.status_code == 502

    snap = upstream.breaker.snapshot()
    assert snap["window_samples"] == 1
    assert snap["window_failures"] == 1


def test_breaker_full_lifecycle_through_gateway():
    """End to end: trip, cooldown, probe, close."""
    import time
    from app.circuit import BreakerState

    STATS.reset()
    routes = RouteTable([
        Route("/cyc", ("http://127.0.0.1:9101",), strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    pools = app.extensions["pools"]
    upstream = pools.pool_for("/cyc").upstreams()[0]

    # Shorten the cooldown for the test
    upstream.breaker.cooldown_seconds = 0.1

    for _ in range(5):
        upstream.breaker.record(False)
    assert upstream.breaker.state() == BreakerState.OPEN

    with app.test_client() as c:
        assert c.get("/cyc/x").status_code == 503

    time.sleep(0.15)
    assert upstream.breaker.state() == BreakerState.HALF_OPEN

    with app.test_client() as c:
        assert c.get("/cyc/x").status_code == 200

    assert upstream.breaker.state() == BreakerState.CLOSED