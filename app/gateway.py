"""The gateway. Reads incoming HTTP, matches a route, forwards to upstream."""

import logging
from flask import Flask, jsonify, request

from app.config import Config
from app.limiter_store import LimiterStore
from app.middleware import RequestContext, run_before, run_after
from app.pool_store import PoolStore
from app.proxy import forward
from app.routes import build_route_table, RouteTable
from app.stats import STATS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mlinzi")


def create_gateway(route_table: RouteTable | None = None) -> Flask:
    app = Flask("mlinzi")
    routes = route_table or build_route_table()
    limiters = LimiterStore(routes)
    pools = PoolStore(routes)

    @app.route("/gateway/health")
    def gateway_health():
        return jsonify({"status": "ok", "routes": len(routes)})

    @app.route("/gateway/routes")
    def gateway_routes():
        return jsonify([
            {
                "prefix": r.prefix,
                "upstreams": pools.snapshot().get(r.prefix, []),
                "strip_prefix": r.strip_prefix,
                "limit": (str(r.limit) if r.limit else None),
            }
            for r in routes.all()
        ])

    @app.route("/gateway/stats")
    def gateway_stats():
        return jsonify(STATS.snapshot())

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def gateway(path: str):
        ctx = RequestContext(
            method=request.method,
            path=request.path,
            client_ip=request.remote_addr or "unknown",
        )
        run_before(ctx)

        route = routes.match(request.path)
        if route is None:
            log.info("no route for %s", request.path)
            resp = jsonify({"error": "no route", "path": request.path})
            resp.status_code = 404
        else:
            ctx.route_prefix = route.prefix

            # Rate limit check before choosing an upstream
            decision = limiters.check(route.prefix, ctx.client_ip)
            ctx.rate_decision = decision

            if decision is not None and not decision.allowed:
                log.info(
                    "rate limit exceeded route=%s client=%s retry_after=%.2fs",
                    route.prefix, ctx.client_ip, decision.retry_after_seconds,
                )
                resp = jsonify({
                    "error": "rate limit exceeded",
                    "route": route.prefix,
                    "limit": decision.limit,
                    "retry_after_seconds": round(decision.retry_after_seconds, 2),
                })
                resp.status_code = 429
            else:
                pool = pools.pool_for(route.prefix)
                upstream = pool.pick() if pool else None

                if upstream is None:
                    log.warning("no healthy upstream for route=%s", route.prefix)
                    resp = jsonify({
                        "error": "no healthy upstream",
                        "route": route.prefix,
                    })
                    resp.status_code = 503
                else:
                    resp = forward(route, upstream.url, timeout=Config.REQUEST_TIMEOUT)

        ctx.status_code = resp.status_code
        run_after(ctx)

        for k, v in ctx.response_headers.items():
            resp.headers[k] = v

        return resp

    # Expose the pool store so the CLI can start/stop health checks
    app.extensions["pools"] = pools
    return app


def main():
    app = create_gateway()
    pools = app.extensions["pools"]
    pools.start_health_checks()
    log.info("mlinzi gateway listening on %s:%d", Config.HOST, Config.PORT)
    try:
        app.run(host=Config.HOST, port=Config.PORT, debug=False, use_reloader=False)
    finally:
        pools.stop_health_checks()


if __name__ == "__main__":
    main()