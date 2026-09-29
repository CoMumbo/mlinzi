# mlinzi

A small API gateway written in Python. Routes incoming requests to upstream
services based on path prefix, applies token-bucket rate limits per route
and per client, load-balances across multiple upstream instances with
background health checks and per-instance circuit breakers, strips path
prefixes when configured, returns proper gateway errors (502, 503, 504)
when upstreams are unavailable, and exposes live request stats.

The name is Swahili for "guard" or "watchman".

## What it does

Data plane (proxying):

- Route table with longest-prefix matching
- Upstream pools with round-robin load balancing
- Background health checks that remove failing instances from rotation
- Per-upstream circuit breakers that trip on real request failures
- Path prefix stripping (e.g. `/api/users/1` becomes `/users/1` on the upstream)
- Query string and request body pass-through
- Hop-by-hop header filtering
- 404 for unmatched routes
- 502 for unreachable upstreams
- 503 when no upstream instance is healthy
- 504 for timeouts

Rate limiting:

- Token bucket algorithm, per route and per client
- Configurable capacity (burst) and refill rate (sustained)
- Returns 429 with `Retry-After` and `X-RateLimit-*` headers
- Independent buckets per route

Management plane:

- `/gateway/health` — liveness and route count
- `/gateway/routes` — list of routes, upstreams, health, breakers, and limits
- `/gateway/stats` — request counters, rate-limit counts, latency percentiles

Middleware, run on every data-plane request:

- `X-Request-ID` — unique per request, attached to the response
- `X-Gateway-Time-Ms` — server-side processing time
- Structured logging with method, path, status, route, duration, client
- Stats collection including rate-limited request count

## Architecture

```
Client --> mlinzi (port 9000) --> upstream pool (port 9001, 9002, ...)

             |-- route table (prefix -> upstream pool + limit)
             |-- rate limiter (token bucket per route + client)
             |-- upstream pool (round-robin across eligible instances)
             |-- health checker (background thread, pings each upstream)
             |-- circuit breaker (per upstream, reactive to request outcomes)
             |-- proxy (forwards via requests)
             |-- middleware (request id, timing, logging, stats)
             |-- management endpoints under /gateway/*
```

The gateway is a single Flask process. Route matching, rate limiting,
upstream selection, forwarding, and middleware all run synchronously per
request. Concurrent requests are handled by Flask's threaded server.
Health checks run on their own daemon threads.

## Running it

```bash
python -m venv .venv
source .venv/Scripts/activate    # Windows Git Bash
# or: source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt
```

In one terminal, start the bundled echo upstream:

```bash
python -m app.echo_upstream
```

In another, start the gateway:

```bash
python -m app.gateway
```

## Try it

The gateway listens on port 9000. Requests are forwarded to the echo
upstream on port 9001.

Forward with prefix preserved:

```bash
curl http://127.0.0.1:9000/echo/hello
```

Forward with prefix stripped:

```bash
curl http://127.0.0.1:9000/api/users/1
```

Trigger the rate limit on `/api` (5 burst, 1/s sustained):

```bash
for i in $(seq 1 8); do
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9000/api/test
done
```

You will see a few 200s followed by 429s.

Inspect the 429 response:

```bash
curl -i http://127.0.0.1:9000/api/test
```

Look for `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `Retry-After`
headers.

Management endpoints:

```bash
curl http://127.0.0.1:9000/gateway/health
curl http://127.0.0.1:9000/gateway/routes
curl http://127.0.0.1:9000/gateway/stats
```

## Route configuration

Routes, upstream pools, and rate limits are defined in `app/routes.py`:

```python
Route(
    prefix="/api",
    upstreams=("http://127.0.0.1:9001", "http://127.0.0.1:9002"),
    strip_prefix=True,
    limit=Limit(capacity=5, refill_per_second=1.0),
)
```

The longest matching prefix wins. `limit=None` means the route is
unlimited. `upstreams` can be one instance or several.

## How rate limiting works

Each route has its own `RateLimiter`. Each client (identified by IP)
gets its own token bucket within that limiter. A bucket starts full at
`capacity`, refills at `refill_per_second`, and each request consumes
one token.

Burst behavior is bounded by `capacity`. Sustained throughput is bounded
by `refill_per_second`. This is the same algorithm used by AWS API
Gateway, Cloudflare, and Stripe.

Limits are small in the default route table so the demo and tests hit
them quickly. Real deployments would set them from config.

## Load balancing and health checks

Each route can point at multiple upstream instances:

```python
Route(
    prefix="/multi",
    upstreams=("http://127.0.0.1:9001", "http://127.0.0.1:9002"),
)
```

The pool round-robins across healthy instances. A background health checker
pings each instance's `/gateway/health` every 5 seconds. Instances that
fail are removed from rotation. When a failing instance starts responding
again, it rejoins automatically.

If no instance in a route's pool is healthy, the gateway returns
**503 Service Unavailable** with a JSON body.

The health checker runs on its own daemon thread and never blocks request
handling. State changes are logged once per transition rather than on
every check.

## Circuit breaker

Each upstream instance has its own circuit breaker. It reacts to actual
request outcomes, not just health-check pings:

- **CLOSED** — normal. Successes and failures are recorded.
- **OPEN** — tripped. No traffic goes to this instance until the cooldown
  elapses.
- **HALF_OPEN** — cooldown over. One trial request is allowed through.
  Success closes the breaker; failure reopens it with a new cooldown.

The breaker trips when the number of failures within a rolling window
meets `failure_threshold`, provided at least `min_samples` outcomes have
been observed. This prevents a single early failure from shutting down a
healthy upstream.

Which responses count:

- 2xx, 3xx: success
- 4xx: ignored (client's fault, not the upstream's)
- 5xx: failure
- 502, 504: failure

**Health checks and the circuit breaker solve different problems.** Health
checks catch instances that are down. The breaker catches instances that
are up but misbehaving — returning 500s on real requests while still
responding to health pings. You need both to keep traffic away from a
broken upstream.

The breaker uses no background thread. State transitions happen lazily
when `state()` or `allow_request()` is called. This keeps the code simple
and avoids a thread per upstream.

## Testing the circuit breaker

The repo includes a test upstream that can be toggled between healthy and
failing modes:

```bash
python -m scripts.flaky_upstream 9002
```

Control it over HTTP:

```bash
curl http://127.0.0.1:9002/_control/fail   # make it return 500s
curl http://127.0.0.1:9002/_control/ok     # make it return 200s
curl http://127.0.0.1:9002/_control/status # show current mode
```

Add a route pointing at both the echo upstream and the flaky one, hit it
enough times to trip the breaker, then watch the routes endpoint show the
breaker in `open` state. Fix the flaky upstream, wait for the cooldown,
and the breaker closes again automatically.

## Tests

```bash
python -m pytest
```

Seventy-three tests covering route matching, prefix stripping, forwarding
through a real echo upstream, middleware headers, stats collection,
gateway error paths (404, 502, 503), the token bucket algorithm including
refill and capacity caps, per-route rate limit isolation, and the circuit
breaker state machine (all transitions, window pruning, half-open probe
semantics).

## Project layout

```
mlinzi/
├── app/
│   ├── gateway.py        Flask app, request handler
│   ├── proxy.py          forwarding logic
│   ├── routes.py         route table with per-route pools and limits
│   ├── upstream.py       upstream instances and round-robin pools
│   ├── health.py         background health checker thread
│   ├── circuit.py        circuit breaker state machine
│   ├── pool_store.py     one pool per route, plus health checkers
│   ├── ratelimit.py      token bucket and limiter registry
│   ├── limiter_store.py  one limiter per route
│   ├── middleware.py     request id, timing, logging, stats, ratelimit headers
│   ├── stats.py          thread-safe request counters
│   ├── config.py         env-based settings
│   └── echo_upstream.py  bundled test upstream
├── scripts/
│   └── flaky_upstream.py toggleable failing upstream for testing
├── tests/
├── docs/
└── requirements.txt
```

## Roadmap

Next sessions:

- Architecture diagram
- `docs/DESIGN.md` and `docs/FAILURES.md`
- End-to-end demo script

## License

MIT