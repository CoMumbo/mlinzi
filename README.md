# mlinzi

A small API gateway written in Python. Routes incoming requests to upstream
services based on path prefix, strips path prefixes when configured, and
returns proper gateway errors (502, 504) when upstreams are unavailable.

The name is Swahili for "guard" or "watchman".

## Status

Day 1 of the build. Forwarding works:

- Route table with longest-prefix matching
- Request forwarding via `requests`
- Path prefix stripping (e.g. `/api/users/1` becomes `/users/1` on the upstream)
- Query string and request body pass-through
- Hop-by-hop header filtering
- 404 for unmatched routes
- 502 for unreachable upstreams
- 504 for timeouts

Rate limiting, middleware, health checks, and load balancing land in
subsequent sessions.

## Running it

```bash
python -m venv .venv
source .venv/Scripts/activate    # Windows Git Bash
# or: source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt