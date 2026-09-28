"""The gateway. Reads incoming HTTP, matches a route, forwards to upstream."""

import logging
from flask import Flask, jsonify, request

from app.config import Config
from app.proxy import forward
from app.routes import build_route_table, RouteTable

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

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def gateway(path: str):
        incoming = request.path
        route = routes.match(incoming)

        if route is None:
            log.info("no route for %s", incoming)
            return jsonify({"error": "no route", "path": incoming}), 404

        return forward(route, timeout=Config.REQUEST_TIMEOUT)

    return app


def main():
    app = create_gateway()
    log.info("mlinzi gateway listening on %s:%d", Config.HOST, Config.PORT)
    app.run(host=Config.HOST, port=Config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()