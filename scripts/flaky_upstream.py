"""A test upstream that can be toggled between healthy and failing.

Run with:
    python -m scripts.flaky_upstream

Control via HTTP:
    GET /_control/ok         switch to returning 200
    GET /_control/fail       switch to returning 500
    GET /_control/status     show current mode

Everything else returns 200 or 500 depending on mode.
"""

import logging
import sys
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("flaky")

app = Flask("flaky")
MODE = {"failing": False}


@app.route("/_control/ok")
def set_ok():
    MODE["failing"] = False
    log.info("mode -> ok")
    return jsonify({"mode": "ok"})


@app.route("/_control/fail")
def set_fail():
    MODE["failing"] = True
    log.info("mode -> fail")
    return jsonify({"mode": "fail"})


@app.route("/_control/status")
def status():
    return jsonify({"mode": "fail" if MODE["failing"] else "ok"})


@app.route("/gateway/health")
def health():
    # Always healthy from the health-checker's point of view.
    # This lets us demonstrate that the circuit breaker reacts to real
    # request failures, not just to health-check pings.
    return jsonify({"status": "ok"})


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def handler(path):
    if MODE["failing"]:
        return jsonify({"error": "simulated failure"}), 500
    return jsonify({"ok": True, "path": "/" + path if path else "/"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9002
    log.info("flaky upstream listening on 127.0.0.1:%d", port)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()