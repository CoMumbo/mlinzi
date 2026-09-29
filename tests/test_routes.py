import pytest

from app.routes import Route, RouteTable, build_route_table


def test_route_matches_exact_prefix():
    r = Route("/api", ("http://x",), strip_prefix=False)
    assert r.matches("/api")
    assert r.matches("/api/users")
    assert not r.matches("/apix")


def test_route_table_empty():
    rt = RouteTable()
    assert rt.match("/anything") is None
    assert len(rt) == 0


def test_route_table_single_match():
    rt = RouteTable([Route("/api", ("http://x",))])
    r = rt.match("/api/users/1")
    assert r is not None
    assert r.prefix == "/api"


def test_longest_prefix_wins():
    rt = RouteTable([
        Route("/api", ("http://general",)),
        Route("/api/users", ("http://specific",)),
    ])
    assert rt.match("/api/users/1").upstreams[0] == "http://specific"
    assert rt.match("/api/orders").upstreams[0] == "http://general"


def test_duplicate_prefix_rejected():
    rt = RouteTable([Route("/api", ("http://a",))])
    with pytest.raises(ValueError):
        rt.add(Route("/api", ("http://b",)))


def test_no_match_returns_none():
    rt = RouteTable([Route("/api", ("http://a",))])
    assert rt.match("/other") is None


def test_default_route_table_has_echo_and_api():
    rt = build_route_table()
    assert len(rt) == 2

    echo = rt.match("/echo/anything")
    assert echo is not None
    assert echo.strip_prefix is False
    assert len(echo.upstreams) == 1

    api = rt.match("/api/users/1")
    assert api is not None
    assert api.strip_prefix is True
    assert len(api.upstreams) == 1


def test_all_returns_copy():
    rt = RouteTable([Route("/a", ("http://a",))])
    routes = rt.all()
    routes.append(Route("/b", ("http://b",)))
    assert len(rt) == 1