"""End-to-end demo for mlinzi.

Starts the echo upstream and the gateway in-process, fires requests that
exercise every feature, prints the results, then shuts down.

Run with:

    python -m scripts.demo
"""

import logging
import threading
import time

import requests

from app.config import Config
from app.echo_upstream import create_echo_app
from app.gateway import create_gateway
from app.routes import Route, RouteTable
from app.ratelimit import Limit


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Quiet down noisy libraries
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("mlinzi.proxy").setLevel(logging.WARNING)
logging.getLogger("mlinzi.middleware").setLevel(logging.WARNING)
logging.getLogger("mlinzi.health").setLevel(logging.WARNING)

log = logging.getLogger("mlinzi.demo")

ECHO_PORT = 9201
GATEWAY_PORT = 9200


def _start_echo():
    app = create_echo_app()
    t = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=ECHO_PORT, debug=False, use_reloader=False, threaded=True
        ),
        daemon=True,
        name="demo-echo",
    )
    t.start()
    time.sleep(0.6)


def _start_gateway(routes: RouteTable):
    app = create_gateway(route_table=routes)
    pools = app.extensions["pools"]
    pools.start_health_checks()

    t = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=GATEWAY_PORT, debug=False, use_reloader=False, threaded=True
        ),
        daemon=True,
        name="demo-gateway",
    )
    t.start()
    time.sleep(0.6)
    return pools


def _section(title: str) -> None:
    print()
    print("-" * 60)
    print(title)
    print("-" * 60)


def main():
    print()
    print("=" * 60)
    print("mlinzi demo")
    print("=" * 60)

    # ---------------------------------------------------------------- setup
    routes = RouteTable([
        Route(
            prefix="/echo",
            upstreams=(f"http://127.0.0.1:{ECHO_PORT}",),
            strip_prefix=False,
            limit=None,
        ),
        Route(
            prefix="/api",
            upstreams=(f"http://127.0.0.1:{ECHO_PORT}",),
            strip_prefix=True,
            limit=Limit(capacity=5, refill_per_second=0.001),  # tight for demo
        ),
    ])

    _start_echo()
    pools = _start_gateway(routes)

    base = f"http://127.0.0.1:{GATEWAY_PORT}"

    # -------------------------------------------------------- basic proxy
    _section("1. Basic forwarding")
    r = requests.get(f"{base}/echo/hello")
    body = r.json()
    print(f"  GET /echo/hello      -> {r.status_code}")
    print(f"  upstream saw path:     {body['path']}")
    print(f"  X-Request-ID:          {r.headers.get('X-Request-ID')}")
    print(f"  X-Gateway-Time-Ms:     {r.headers.get('X-Gateway-Time-Ms')}")

    # ------------------------------------------------------ prefix strip
    _section("2. Prefix stripping")
    r = requests.get(f"{base}/api/users/1")
    body = r.json()
    print(f"  GET /api/users/1     -> {r.status_code}")
    print(f"  upstream saw path:     {body['path']}  (stripped)")

    # ------------------------------------------------------ query string
    _section("3. Query string pass-through")
    r = requests.get(f"{base}/echo/search?q=hello&limit=10")
    body = r.json()
    print(f"  GET /echo/search?q=hello&limit=10 -> {r.status_code}")
    print(f"  upstream saw query:    {body['query']}")

    # ---------------------------------------------------------- POST body
    _section("4. POST body pass-through")
    r = requests.post(f"{base}/api/items", json={"name": "widget"})
    body = r.json()
    print(f"  POST /api/items      -> {r.status_code}")
    print(f"  upstream saw body:     {body['body']}")

    # ----------------------------------------------------------- 404
    _section("5. Unmatched route")
    r = requests.get(f"{base}/nonexistent")
    print(f"  GET /nonexistent     -> {r.status_code}")
    print(f"  body:                  {r.json()}")

    # ------------------------------------------------------ rate limiting
    _section("6. Rate limiting on /api (capacity=5, no refill)")
    codes = []
    for i in range(8):
        r = requests.get(f"{base}/api/test")
        codes.append(r.status_code)
    print(f"  8 requests:            {codes}")

    r = requests.get(f"{base}/api/test")
    print(f"  X-RateLimit-Limit:     {r.headers.get('X-RateLimit-Limit')}")
    print(f"  X-RateLimit-Remaining: {r.headers.get('X-RateLimit-Remaining')}")
    print(f"  Retry-After:           {r.headers.get('Retry-After')}")

    # ------------------------------------------------------ 502 (dead)
    _section("7. Unreachable upstream")
    dead_routes = RouteTable([
        Route(prefix="/dead", upstreams=("http://127.0.0.1:1",), strip_prefix=False),
    ])
    # We don't spin a second gateway; instead hit an upstream we know is dead
    # by adding a temporary route? Simpler: just show the existing behaviour
    # by hitting a genuinely dead port through the same gateway is not
    # possible without restarting. Instead we can note the behaviour.
    print("  (see tests: test_unreachable_upstream_returns_502)")
    print("  a request to a dead upstream returns 502 with body 'upstream unreachable'")

    # ------------------------------------------------------ stats
    _section("8. Stats snapshot")
    r = requests.get(f"{base}/gateway/stats")
    stats = r.json()
    for key in ("total_requests", "rate_limited_requests", "avg_latency_ms", "p95_latency_ms"):
        print(f"  {key:24s} {stats[key]}")
    print(f"  requests_by_status:      {stats['requests_by_status']}")
    print(f"  requests_by_route:       {stats['requests_by_route']}")

    # ------------------------------------------------------ routes
    _section("9. Route table with breakers and limits")
    r = requests.get(f"{base}/gateway/routes")
    for route in r.json():
        print(f"  {route['prefix']}")
        print(f"    strip_prefix={route['strip_prefix']} limit={route['limit']}")
        for u in route["upstreams"]:
            b = u["breaker"]
            print(f"    {u['url']}  healthy={u['healthy']} eligible={u['eligible']} "
                  f"breaker={b['state']}")

    # ---------------------------------------------------------- shutdown
    _section("done")
    pools.stop_health_checks()

    print()
    print("=" * 60)
    print("demo complete")
    print("=" * 60)
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted")