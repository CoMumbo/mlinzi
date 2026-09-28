"""A tiny upstream service for testing mlinzi.

Echoes request method, path, headers, and body back as JSON.
Run with: python -m app.echo_upstream
"""

import logging
from flask import Flask, jsonify, request

from app.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mlinzi.echo")


def create_echo_app() -> Flask:
    app = Flask("mlinzi-echo")

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def echo(path: str):
        body = request.get_data(as_text=True)
        return jsonify({
            "upstream": "echo",
            "method": request.method,
            "path": "/" + path if path else "/",
            "query": request.query_string.decode("utf-8"),
            "headers": {k: v for k, v in request.headers.items()},
            "body": body,
        })

    return app


def main():
    app = create_echo_app()
    log.info("echo upstream listening on 127.0.0.1:%d", Config.ECHO_PORT)
    app.run(host="127.0.0.1", port=Config.ECHO_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()