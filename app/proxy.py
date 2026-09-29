"""Forward an incoming Flask request to an upstream instance."""

import logging

import requests
from flask import Response, request

from app.routes import Route

log = logging.getLogger("mlinzi.proxy")

# Hop-by-hop headers that should not be forwarded between client and upstream.
# See RFC 7230 section 6.1.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _filter_request_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def _filter_response_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def _target_path(route: Route, incoming_path: str) -> str:
    """Compute the path to forward to the upstream.

    If the route says to strip the prefix, remove it from the incoming path.
    """
    if not route.strip_prefix:
        return incoming_path

    prefix = route.prefix
    if incoming_path == prefix:
        return "/"
    if incoming_path.startswith(prefix + "/"):
        return incoming_path[len(prefix):]
    return incoming_path


def _target_url(upstream_base: str, route: Route, incoming_path: str, query_string: str) -> str:
    path = _target_path(route, incoming_path)
    url = upstream_base.rstrip("/") + path
    if query_string:
        url = f"{url}?{query_string}"
    return url


def forward(route: Route, upstream_base: str, timeout: float) -> Response:
    """Forward the current Flask request to a specific upstream instance.

    `upstream_base` is one instance URL (e.g. "http://127.0.0.1:9001"). The
    caller (the gateway) is responsible for picking which instance to use.

    Returns a Flask Response carrying the upstream's status, headers, and body.
    """
    incoming_path = request.path
    query_string = request.query_string.decode("utf-8")
    url = _target_url(upstream_base, route, incoming_path, query_string)

    headers = _filter_request_headers(request.headers)
    body = request.get_data() if request.method in ("POST", "PUT", "PATCH", "DELETE") else None

    log.info("forward %s %s -> %s", request.method, incoming_path, url)

    try:
        upstream_resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=body,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.Timeout:
        log.warning("upstream timeout: %s", url)
        return Response("upstream timeout\n", status=504, mimetype="text/plain")
    except requests.ConnectionError as e:
        log.warning("upstream connection error: %s (%s)", url, e)
        return Response("upstream unreachable\n", status=502, mimetype="text/plain")
    except requests.RequestException as e:
        log.exception("upstream request failed: %s", url)
        return Response(f"gateway error: {e}\n", status=502, mimetype="text/plain")

    resp_headers = _filter_response_headers(upstream_resp.headers)

    return Response(
        upstream_resp.content,
        status=upstream_resp.status_code,
        headers=resp_headers,
    )