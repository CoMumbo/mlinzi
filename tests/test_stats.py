import time

from app.stats import Stats


def test_record_and_snapshot_basic():
    s = Stats()
    s.record("/a", 200, 5.0)
    s.record("/a", 200, 7.0)
    s.record("/b", 404, 1.0)

    snap = s.snapshot()
    assert snap["total_requests"] == 3
    assert snap["requests_by_status"]["200"] == 2
    assert snap["requests_by_status"]["404"] == 1
    assert snap["requests_by_route"]["/a"] == 2
    assert snap["requests_by_route"]["/b"] == 1


def test_unmatched_route_bucket():
    s = Stats()
    s.record(None, 404, 2.0)
    snap = s.snapshot()
    assert snap["requests_by_route"]["_unmatched"] == 1


def test_avg_latency():
    s = Stats()
    s.record("/a", 200, 10.0)
    s.record("/a", 200, 20.0)
    s.record("/a", 200, 30.0)
    assert s.snapshot()["avg_latency_ms"] == 20.0


def test_avg_latency_empty():
    s = Stats()
    assert s.snapshot()["avg_latency_ms"] == 0.0


def test_p95_with_enough_samples():
    s = Stats()
    for i in range(1, 101):  # 1..100
        s.record("/a", 200, float(i))
    p95 = s.snapshot()["p95_latency_ms"]
    # Linear interpolation: near 95
    assert 94.0 <= p95 <= 96.0


def test_latency_window_is_bounded():
    s = Stats(latency_window=5)
    for i in range(100):
        s.record("/a", 200, float(i))
    # Only the last 5 samples should be kept; avg of 95..99 = 97
    assert s.snapshot()["avg_latency_ms"] == 97.0


def test_reset_clears_everything():
    s = Stats()
    s.record("/a", 200, 5.0)
    s.reset()
    snap = s.snapshot()
    assert snap["total_requests"] == 0
    assert snap["requests_by_status"] == {}
    assert snap["requests_by_route"] == {}


def test_uptime_is_positive():
    s = Stats()
    time.sleep(0.01)
    assert s.snapshot()["uptime_seconds"] > 0