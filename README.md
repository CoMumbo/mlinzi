# mlinzi

A small API gateway written in Python. Routes incoming requests to upstream
services based on path prefix, applies token-bucket rate limits per route
and per client, strips path prefixes when configured, returns proper
gateway errors (502, 504) when upstreams are unavailable, and exposes
live request stats.

The name is Swahili for "guard" or "watchman".

## What it does

Data plane (proxying):

- Route table with longest-prefix matching
- Request forwarding via `requests`
- Path prefix stripping (e.g. `/api/users/1` becomes `/users/1` on the upstream)
- Query string and request body pass-through
- Hop-by-hop header filtering
- 404 for unmatched routes
- 502 for unreachable upstreams
- 504 for timeouts

Rate limiting:

- Token bucket algorithm, per route and per client
- Configurable capacity (burst) and refill rate (sustained)
- Returns 429 with `Retry-After` and `X-RateLimit-*` headers
- Independent buckets per route

Management plane:

- `/gateway/health` — liveness and route count
- `/gateway/routes` — list of configured routes and their limits
- `/gateway/stats` — request counters, rate-limit counts, latency percentiles

Middleware, run on every data-plane request:

- `X-Request-ID` — unique per request, attached to the response
- `X-Gateway-Time-Ms` — server-side processing time
- Structured logging with method, path, status, route, duration, client
- Stats collection including rate-limited request count

## Architecture

```
Client --> mlinzi (port 9000) --> upstream (port 9001, etc.)

             |-- route table (prefix -> upstream + limit)
             |-- rate limiter (token bucket per route + client)
             |-- proxy (forwards via requests)
             |-- middleware (request id, timing, logging, stats)
             |-- management endpoints under /gateway/*
```

The gateway is a single Flask process. Route matching, rate limiting,
forwarding, and middleware all run synchronously per request. Concurrent
requests are handled by Flask's threaded server.

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

Routes and rate limits are defined in `app/routes.py`:

```python
Route(
    prefix="/api",
    upstream="http://127.0.0.1:9001",
    strip_prefix=True,
    limit=Limit(capacity=5, refill_per_second=1.0),
)
```

The longest matching prefix wins. `limit=None` means the route is
unlimited.

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

## Tests

```bash
python -m pytest
```

Fifty-one tests covering route matching, prefix stripping, forwarding
through a real echo upstream, middleware headers, stats collection,
gateway error paths (404, 502), and the token bucket algorithm including
refill, capacity caps, and per-key isolation.

## Project layout

```
mlinzi/
├── app/
│   ├── gateway.py        Flask app, request handler
│   ├── proxy.py          forwarding logic
│   ├── routes.py         route table with per-route limits
│   ├── ratelimit.py      token bucket and limiter registry
│   ├── limiter_store.py  one limiter per route
│   ├── middleware.py     request id, timing, logging, stats, ratelimit headers
│   ├── stats.py          thread-safe request counters
│   ├── config.py         env-based settings
│   └── echo_upstream.py  bundled test upstream
├── tests/
├── docs/
└── requirements.txt
```

## Roadmap

Next sessions:

- Load balancing across multiple upstreams
- Upstream health checks
- Circuit breaker
- Architecture diagram
- `docs/DESIGN.md` and `docs/FAILURES.md`

## License

MIT