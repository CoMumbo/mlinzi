"""The gateway. Reads incoming HTTP, matches a route, forwards to upstream."""

import logging
from flask import Flask, Response, jsonify, request

from app.config import Config
from app.middleware import RequestContext, run_before, run_after
from app.proxy import forward
from app.routes import build_route_table, RouteTable
from app.stats import STATS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mlinzi")


def create_gateway(route_table: RouteTable | None = None) -> Flask:
    app = Flask("mlinzi")
    routes = route_table or build_route_table()

    @app.route("/gateway/health")
    def gateway_health():
        return jsonify({
            "status": "ok",
            "routes": len(routes),
        })

    @app.route("/gateway/routes")
    def gateway_routes():
        return jsonify([
            {
                "prefix": r.prefix,
                "upstream": r.upstream,
                "strip_prefix": r.strip_prefix,
            }
            for r in routes.all()
        ])

    @app.route("/gateway/stats")
    def gateway_stats():
        return jsonify(STATS.snapshot())

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def gateway(path: str):
        ctx = RequestContext(method=request.method, path=request.path)
        run_before(ctx)

        route = routes.match(request.path)
        if route is None:
            log.info("no route for %s", request.path)
            resp = jsonify({"error": "no route", "path": request.path})
            resp.status_code = 404
        else:
            ctx.route_prefix = route.prefix
            resp = forward(route, timeout=Config.REQUEST_TIMEOUT)

        ctx.status_code = resp.status_code
        run_after(ctx)

        # Attach middleware-produced headers to the response
        for k, v in ctx.response_headers.items():
            resp.headers[k] = v

        return resp

    return app


def main():
    app = create_gateway()
    log.info("mlinzi gateway listening on %s:%d", Config.HOST, Config.PORT)
    app.run(host=Config.HOST, port=Config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()