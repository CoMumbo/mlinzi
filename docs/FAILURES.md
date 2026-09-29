# Failure modes

This document walks through how mlinzi behaves when things go wrong. It
covers failure cases that were tested, cases that were reasoned about
without a test, and cases that are known gaps.

## Upstream returns 5xx

**Scenario.** An upstream is up and responds, but returns 500s.

**What happens.** The response is passed to the client unchanged. The
upstream's circuit breaker records a failure. After enough failures within
the window, the breaker trips and removes the instance from rotation.

**What this project does about it.** Circuit breaker (see DESIGN.md). The
threshold and window are configurable per instance.

## Upstream is unreachable

**Scenario.** An upstream process is down. TCP connections are refused or
time out.

**What happens.** Two layers react:

- Health checks detect the failure within `health_interval` seconds and
  mark the instance unhealthy.
- Requests that are in flight during the interval get a **502** with body
  "upstream unreachable". The breaker records a failure.

Once all instances of a route are unhealthy, requests return **503** with
"no healthy upstream".

**What this project does about it.** Both mechanisms above. Either is
sufficient; both together give faster detection and better behavior.

## Upstream is slow

**Scenario.** An upstream responds, but takes longer than
`MLINZI_REQUEST_TIMEOUT` (default 5s).

**What happens.** `requests` raises `Timeout`, and `forward()` returns
**504 Gateway Timeout**. The breaker records a failure.

**What this project does about it.** Timeout handling and 504. The
breaker treats slow responses as failures, which is correct — a
consistently slow upstream is effectively broken from the client's
perspective.

**What's missing.** Per-route timeouts. Currently all routes share one
timeout. A real gateway would let each route specify its own.

## Client hammers a route

**Scenario.** A single client sends a burst of requests to `/api`.

**What happens.** The token bucket for that (route, client) pair drains.
Once empty, further requests get **429** with `Retry-After` and
`X-RateLimit-Remaining: 0`.

**What this project does about it.** Rate limiting. The client's own
traffic is limited without affecting other clients.

## Burst from many clients

**Scenario.** Many clients send requests simultaneously, each within
their own rate limit.

**What happens.** No limiter trips — each client is within its budget.
The upstream takes the full load.

**What this project does about it.** Nothing at the gateway level. This is
a case for load balancing: if the route has multiple upstreams, the pool
distributes load. If all upstreams are at capacity, they start returning
5xx, and the circuit breakers eventually trip.

**What's missing.** A global rate limit (per route, across all clients).
If the upstream can only handle 1000 req/s regardless of client count, the
gateway should cap total traffic to that route. Currently we cap per
client, not per route.

## Worker thread exhaustion

**Scenario.** Many concurrent requests tie up Flask's thread pool.

**What happens.** Requests queue. Latency climbs. Eventually, new requests
wait for a thread and may time out at the client.

**What this project does about it.** Nothing. Flask's threaded server
spawns threads on demand and doesn't cap them by default.

**What a real system does.** Cap the thread pool and queue depth. Reject
with 503 when the queue is full (backpressure). Or move to an event loop
so thread count is not the limit.

## Gateway process crashes

**Scenario.** The gateway process is killed.

**What happens.** All in-flight requests fail. The process restarts (if
under a supervisor) and starts fresh — no state is persisted.

**What this project does about it.** Nothing.

**Why this is acceptable here.** The gateway is stateless by design. No
session state, no cache to warm. A restart is nearly transparent to
clients that retry.

**What this is not acceptable for.** If rate limit state needs to survive
restarts (e.g. to enforce a per-day quota), the token buckets would need
to be persisted. That's not implemented.

## Circuit breaker thundering herd

**Scenario.** A breaker is open for a healthy upstream (false positive).
Traffic is diverted to remaining instances, which then become overloaded.

**What happens.** Cascading failure — the remaining instances trip their
own breakers, and the route returns 503.

**What this project does about it.** Nothing.

**What a real system does.** Rate-limit the recovery: when the cooldown
expires, slowly reintroduce traffic instead of putting the instance back
into full rotation immediately. This is sometimes called a "slow start"
after recovery.

## Health check false positives

**Scenario.** An upstream's health endpoint is slow or flaky, but the
actual request path is fine.

**What happens.** The health checker marks the instance unhealthy and
removes it from rotation. All traffic goes to the other instances.

**What this project does about it.** Requires multiple consecutive
failures? No — currently a single health check failure is enough to
mark unhealthy.

**What a real system does.** Require N consecutive failures before
marking unhealthy. This prevents a single transient timeout from taking
an instance out.

**Fix if needed.** Add a `consecutive_failures >= N` check in
`HealthChecker._check_one` before calling `mark_unhealthy`.

## Long-running requests during cooldown

**Scenario.** A request is in flight when the breaker trips.

**What happens.** The request completes normally. When it returns, the
breaker records the outcome. If it was a success and the breaker is now
OPEN, the record does nothing (OPEN state ignores new outcomes until
cooldown).

**What this project does about it.** Nothing needed. The behavior is
correct.

## Config errors at startup

**Scenario.** `MLINZI_PORT` is set to a port already in use.

**What happens.** Flask fails to bind and the process exits with a
traceback.

**What this project does about it.** Nothing.

**What a real system does.** Validate config at startup — check that
ports are numeric and in range, timeouts are positive, and required env
vars are present. Fail with a clear error before attempting to bind.

## What is not a failure mode

Things that are sometimes assumed to be problems but aren't:

- **Requests landing on different upstreams.** That's load balancing.
- **Some requests taking longer than others.** Upstreams vary.
- **Occasional 429s for one client.** That's the rate limiter working.
- **The `/gateway/*` endpoints not being counted in stats.** Management
  traffic isn't data traffic. On purpose.
- **Buckets not being cleaned up.** Correct until memory pressure matters,
  which it doesn't at this scale.