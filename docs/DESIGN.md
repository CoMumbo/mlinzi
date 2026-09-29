# Design decisions

This document records the significant design choices behind mlinzi and the
trade-offs behind each. It's meant to be read alongside the code, not as a
tutorial.

## Proxying via Flask + requests

The gateway is a Flask app. Each incoming request is handled synchronously:
read the request, pick an upstream, forward with `requests`, return the
response. Flask's threaded mode handles concurrency (one thread per
in-flight request).

Why not async (aiohttp/httpx):

- Readability. The flow is linear and easy to follow.
- The concurrency model (thread per request) is simpler to reason about.

Why this is a limitation at scale:

- One thread per request. Under thousands of concurrent connections,
  thread overhead becomes significant.
- Real gateways (nginx, Envoy, Traefik) use an event loop and handle tens
  of thousands of concurrent connections on a handful of threads.

Migrating to `httpx` with `async`/`await` would be a mechanical change:
`forward()` becomes `async def`, Flask becomes something like Starlette or
FastAPI, and the middleware chain becomes async-aware. Not done here
because the added complexity isn't worth it for a learning project.

## Token bucket rate limiting

Rate limits use the token bucket algorithm:

- A bucket starts full at `capacity` tokens.
- It refills at `refill_per_second`.
- Each request consumes one token.
- If no tokens are available, the request is rejected with 429.

Why token bucket over fixed window:

- **Fixed window** counts requests per time unit (e.g. 100 per minute). It
  allows a burst at the boundary: 100 requests at 11:59:59 and 100 at
  12:00:00 equals 200 requests in two seconds.
- **Token bucket** allows bursts up to `capacity` but enforces a steady
  average of `refill_per_second`. It's what AWS API Gateway, Cloudflare,
  and Stripe use.

Why not leaky bucket: a leaky bucket is a token bucket with a queue. It
smooths traffic perfectly but adds queue delay. Rate limiting typically
wants the reject-immediately behavior, not queueing.

## Rate limiter keying

Each route has its own `RateLimiter`. Each client (currently the request's
IP address) gets its own bucket within that limiter.

Why per-route, not a single global limiter:

- Different routes have different cost/risk profiles. A cheap read endpoint
  should have a higher limit than an expensive write endpoint.
- A global limiter would let one route's traffic exhaust another's budget.

Why not per-API-key:

- The gateway doesn't currently authenticate requests. Adding API keys
  means an auth layer, key rotation, revocation, and metering. Out of scope
  here, but the design supports it — the client key is just a string passed
  to `RateLimiter.check()`.

## Bucket cleanup

Buckets are created lazily on first request from a given key and never
removed. Over a long run with many distinct clients, the `_buckets` dict
grows unbounded.

What a real system does:

- Evict idle buckets (e.g. LRU with a time window).
- Cap the total number of buckets and evict oldest.
- Use a probabilistic sketch for approximate counting when memory matters.

Not implemented here because:
- The failure mode (memory growth) is slow and only matters under attack.
- A simple fix (periodic sweep of buckets that haven't been touched in N
  minutes) is easy to add but wasn't necessary for the project's scope.

## Round-robin load balancing

`UpstreamPool.pick()` walks the pool from `_next_index` forward until it
finds an eligible instance, then returns it. Eligible means healthy AND
not circuit-broken.

Why round-robin over least-connections or random:

- Round-robin is stateless and fair under uniform load. It's the right
  default.
- Least-connections requires per-request tracking on every instance. It's
  better under highly variable load but more complex.
- Random is nearly as good as round-robin under uniform load and trivially
  distributed, but produces less even distributions in small pools.

Not implemented: weighted round-robin (where an instance gets 2x traffic
because it's faster) and consistent hashing (where the same key always
goes to the same instance, useful for caching).

## Health checks

A background thread pings each upstream's `/gateway/health` every 5
seconds. Failing instances are marked unhealthy and removed from rotation.
Recovered instances rejoin automatically.

Why a background thread, not a request-time check:

- Request-time checks add latency to every request.
- Background checks detect failure even when no traffic is flowing.
- State changes are cheap to apply — just a boolean flip.

Why 5 seconds:

- Short enough to detect failures quickly.
- Long enough not to flood an already-struggling upstream.
- Configurable via `HealthChecker.interval`.

Not implemented: adaptive intervals (poll faster when unhealthy, slower
when healthy) and check jitter (to avoid all gateways hitting the same
upstream at the same instant).

## Circuit breaker

Each upstream has a `CircuitBreaker` with three states: CLOSED, OPEN,
HALF_OPEN. Trips when failures in a rolling window exceed a threshold.
Reacts to real request outcomes, not health pings.

Why a rolling window, not a consecutive-failure counter:

- A rolling window catches "5 failures out of the last 20 requests" —
  which is exactly what indicates a degraded upstream.
- A consecutive counter overreacts to transient blips.

Why `min_samples`:

- Without it, the first failure of a fresh breaker would trip it.
- `min_samples=5` means at least 5 outcomes have to occur before the
  breaker can trip. Prevents false positives during warmup.

Why 4xx is not counted as a failure:

- A 4xx means the client sent a bad request, not that the upstream is
  broken. Counting 4xx would let a misbehaving client trip the breaker
  and take an upstream out of rotation for everyone.

Why no background thread:

- State transitions happen lazily on read (`state()` or `allow_request()`).
- No thread means no per-upstream overhead. With hundreds of upstreams,
  the thread count would matter.

Why half-open allows only one probe:

- If we let all queued requests through on recovery, a still-broken
  upstream gets hammered the instant the cooldown expires.
- One probe at a time gives the upstream a chance to prove itself without
  amplification.

## Middleware chain

Request handling is a chain of small functions: BEFORE (request ID, start
timer), then the handler logic, then AFTER (stop timer, attach rate-limit
headers, log, record stats). Adding new behavior means appending to a list.

Why this shape:

- Each middleware does one thing and is independently testable.
- Order is explicit — BEFORE is a list, AFTER is a list.
- The same pattern is used by every web framework (Express middleware,
  Django middleware, Envoy HTTP filters).

Why a `RequestContext` object instead of passing parameters:

- Adding a new piece of middleware that needs a new field means adding a
  dataclass field, not threading another parameter through every function.
- The context is the single source of truth for what happened in a request.

## Status code semantics

- **404** for unmatched routes — the gateway has no route for this path.
- **429** for rate-limited requests — the client exceeded its limit.
- **502** for bad responses from an upstream — we reached the upstream but
  it failed.
- **503** for no eligible upstream — every instance is unhealthy or
  circuit-broken.
- **504** for upstream timeouts — we waited too long for a response.

The 502 vs 503 distinction matters. 502 means "I tried an upstream and it
broke." 503 means "there was no upstream to try." Real gateways
distinguish these and clients can respond differently (retry vs back off).

## What was deliberately left out

- **Authentication and authorization.** The gateway is open.
- **TLS termination.** Runs over plain HTTP.
- **WebSocket proxying.** Only HTTP request/response.
- **Response caching.** Every request goes to an upstream.
- **Retries.** If an upstream fails, we return the error. A retry layer
  would go between proxy and pool.
- **Request/response transformation.** No header rewriting, no body
  rewriting.
- **Distributed tracing.** Request IDs are propagated, but there's no
  OpenTelemetry integration.
- **Config hot-reload.** Routes and limits are compiled into the process
  at startup.

Each of these is a well-understood feature that could be added without
restructuring the code. Leaving them out keeps the project focused on the
core data plane.