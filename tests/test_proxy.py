import threading
import time

import pytest
from flask import Flask

from app.config import Config
from app.echo_upstream import create_echo_app
from app.gateway import create_gateway
from app.routes import Route, RouteTable
from app.stats import STATS


# --- Fixture: spin up a real echo upstream in a background thread ----------

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
    time.sleep(0.5)  # let it bind
    yield f"http://127.0.0.1:{port}"


@pytest.fixture
def gateway_client(echo_upstream):
    """A Flask test client for the gateway wired to the real echo upstream."""
    STATS.reset()
    routes = RouteTable([
        Route("/echo", echo_upstream, strip_prefix=False),
        Route("/api", echo_upstream, strip_prefix=True),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# --- Route matching through the gateway ------------------------------------

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


# --- Forwarding ------------------------------------------------------------

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


# --- Middleware headers ----------------------------------------------------

def test_response_includes_request_id_and_timing(gateway_client):
    r = gateway_client.get("/echo/hello")
    assert "X-Request-ID" in r.headers
    assert "X-Gateway-Time-Ms" in r.headers
    assert len(r.headers["X-Request-ID"]) == 16


# --- Error paths -----------------------------------------------------------

def test_unmatched_path_returns_404(gateway_client):
    r = gateway_client.get("/nonexistent")
    assert r.status_code == 404
    assert r.get_json()["error"] == "no route"


def test_unreachable_upstream_returns_502():
    """Point a route at a port that isn't listening."""
    STATS.reset()
    routes = RouteTable([
        Route("/dead", "http://127.0.0.1:1", strip_prefix=False),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/dead/anything")
    assert r.status_code == 502
    assert b"unreachable" in r.data
    # --- Rate limiting ---------------------------------------------------------

def test_rate_limit_allows_burst_then_429():
    """With capacity=2, first two requests pass, third is rate limited."""
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/limited", "http://127.0.0.1:9101", strip_prefix=False,
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
        Route("/rl", "http://127.0.0.1:9101", strip_prefix=False,
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
        Route("/rl", "http://127.0.0.1:9101", strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        c.get("/rl/x")  # consumes the only token
        r = c.get("/rl/y")
        assert r.status_code == 429
        assert r.headers["X-RateLimit-Limit"] == "1"
        assert r.headers["X-RateLimit-Remaining"] == "0"
        assert "Retry-After" in r.headers


def test_rate_limit_is_per_route():
    """Two routes have independent buckets, even from the same client."""
    from app.ratelimit import Limit

    STATS.reset()
    routes = RouteTable([
        Route("/a", "http://127.0.0.1:9101", strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
        Route("/b", "http://127.0.0.1:9101", strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        assert c.get("/a/1").status_code == 200
        assert c.get("/a/2").status_code == 429
        # /b has its own bucket, still fresh
        assert c.get("/b/1").status_code == 200


def test_unlimited_route_has_no_ratelimit_headers():
    """Routes without a limit shouldn't get X-RateLimit-* headers."""
    STATS.reset()
    routes = RouteTable([
        Route("/free", "http://127.0.0.1:9101", strip_prefix=False, limit=None),
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
        Route("/s", "http://127.0.0.1:9101", strip_prefix=False,
              limit=Limit(capacity=1, refill_per_second=0.001)),
    ])
    app = create_gateway(route_table=routes)
    app.config["TESTING"] = True

    with app.test_client() as c:
        c.get("/s/1")
        c.get("/s/2")  # 429
        c.get("/s/3")  # 429

        snap = c.get("/gateway/stats").get_json()
    assert snap["rate_limited_requests"] == 2
    assert snap["requests_by_status"]["429"] == 2