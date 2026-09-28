# mlinzi

A small API gateway written in Python. Routes incoming requests to upstream
services based on path prefix, strips path prefixes when configured, returns
proper gateway errors (502, 504) when upstreams are unavailable, and exposes
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

Management plane:

- `/gateway/health` — liveness and route count
- `/gateway/routes` — list of configured routes
- `/gateway/stats` — live request counters and latency percentiles

Middleware, run on every data-plane request:

- `X-Request-ID` — unique per request, attached to the response
- `X-Gateway-Time-Ms` — server-side processing time
- Structured logging with method, path, status, route, and duration
- Stats collection (total, by status, by route, avg/p95/p99 latency)

## Architecture

```
Client --> mlinzi (port 9000) --> upstream (port 9001, etc.)

             |-- route table (prefix -> upstream)
             |-- proxy (forwards via requests)
             |-- middleware (request id, timing, logging, stats)
             |-- management endpoints under /gateway/*
```

The gateway is a single Flask process. Route matching, forwarding, and
middleware all run synchronously per request. Concurrent requests are
handled by Flask's threaded server.

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

Query string pass-through:

```bash
curl "http://127.0.0.1:9000/echo/search?q=hello&limit=10"
```

POST with a body:

```bash
curl -X POST http://127.0.0.1:9000/api/items \
  -H "Content-Type: application/json" \
  -d '{"name": "widget"}'
```

Unmatched route:

```bash
curl http://127.0.0.1:9000/unknown/path
```

Management endpoints:

```bash
curl http://127.0.0.1:9000/gateway/health
curl http://127.0.0.1:9000/gateway/routes
curl http://127.0.0.1:9000/gateway/stats
```

Every forwarded response includes:

```
X-Request-ID: 0a93dfe7a53549c5
X-Gateway-Time-Ms: 28.33
```

## Route configuration

Routes are defined in `app/routes.py`:

```python
Route(prefix="/echo", upstream="http://127.0.0.1:9001", strip_prefix=False)
Route(prefix="/api",  upstream="http://127.0.0.1:9001", strip_prefix=True)
```

The longest matching prefix wins. `/api` provides a default for everything
under `/api`, but a more specific route like `/api/users` would override it
for that subtree.

## Tests

```bash
python -m pytest
```

Thirty-three tests covering route matching, prefix stripping, forwarding
through a real echo upstream, middleware headers, stats collection, and
gateway error paths (404, 502).

## Project layout

```
mlinzi/
├── app/
│   ├── gateway.py        Flask app, request handler
│   ├── proxy.py          forwarding logic
│   ├── routes.py         route table (prefix -> upstream)
│   ├── middleware.py     request id, timing, logging, stats
│   ├── stats.py          thread-safe request counters
│   ├── config.py         env-based settings
│   └── echo_upstream.py  bundled test upstream
├── tests/
├── docs/
└── requirements.txt
```

## Roadmap

Next sessions:

- Rate limiting (token bucket, per-IP and per-route)
- Load balancing across multiple upstreams
- Upstream health checks
- Circuit breaker
- Architecture diagram

## License

MIT