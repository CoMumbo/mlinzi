import time

import pytest

from app.circuit import BreakerState, CircuitBreaker


def test_starts_closed():
    cb = CircuitBreaker()
    assert cb.state() == BreakerState.CLOSED


def test_allows_when_closed():
    cb = CircuitBreaker()
    assert cb.allow_request() is True


def test_does_not_trip_below_min_samples():
    """Even with all failures, doesn't trip until min_samples seen."""
    cb = CircuitBreaker(failure_threshold=3, min_samples=5, cooldown_seconds=100.0)
    for _ in range(3):
        cb.record(False)
    assert cb.state() == BreakerState.CLOSED


def test_trips_when_threshold_and_min_samples_met():
    cb = CircuitBreaker(failure_threshold=3, min_samples=3, cooldown_seconds=100.0)
    for _ in range(3):
        cb.record(False)
    assert cb.state() == BreakerState.OPEN


def test_does_not_trip_below_failure_threshold():
    cb = CircuitBreaker(failure_threshold=5, min_samples=3, cooldown_seconds=100.0)
    for _ in range(3):
        cb.record(False)
    assert cb.state() == BreakerState.CLOSED


def test_open_blocks_requests():
    cb = CircuitBreaker(failure_threshold=2, min_samples=2, cooldown_seconds=100.0)
    cb.record(False)
    cb.record(False)
    assert cb.state() == BreakerState.OPEN
    assert cb.allow_request() is False


def test_auto_half_open_after_cooldown():
    cb = CircuitBreaker(failure_threshold=1, min_samples=1, cooldown_seconds=0.1)
    cb.record(False)
    assert cb.state() == BreakerState.OPEN
    time.sleep(0.15)
    assert cb.state() == BreakerState.HALF_OPEN


def test_half_open_allows_one_probe_at_a_time():
    cb = CircuitBreaker(failure_threshold=1, min_samples=1, cooldown_seconds=0.1)
    cb.record(False)
    time.sleep(0.15)

    assert cb.allow_request() is True    # first probe
    assert cb.allow_request() is False   # second blocked


def test_half_open_probe_success_closes_breaker():
    cb = CircuitBreaker(failure_threshold=1, min_samples=1, cooldown_seconds=0.1)
    cb.record(False)
    time.sleep(0.15)

    assert cb.allow_request() is True
    cb.record(True)
    assert cb.state() == BreakerState.CLOSED
    assert cb.allow_request() is True


def test_half_open_probe_failure_reopens_breaker():
    cb = CircuitBreaker(failure_threshold=1, min_samples=1, cooldown_seconds=0.1)
    cb.record(False)
    time.sleep(0.15)

    assert cb.allow_request() is True
    cb.record(False)
    assert cb.state() == BreakerState.OPEN


def test_force_open_for_tests():
    cb = CircuitBreaker()
    cb.force_open()
    assert cb.state() == BreakerState.OPEN


def test_reset_clears_state():
    cb = CircuitBreaker(failure_threshold=1, min_samples=1)
    cb.record(False)
    assert cb.state() == BreakerState.OPEN
    cb.reset()
    assert cb.state() == BreakerState.CLOSED


def test_snapshot_shape():
    cb = CircuitBreaker(failure_threshold=3, min_samples=3)
    cb.record(False)
    cb.record(True)
    snap = cb.snapshot()
    assert snap["state"] == "closed"
    assert snap["window_samples"] == 2
    assert snap["window_failures"] == 1
    assert snap["failure_threshold"] == 3
    assert "cooldown_remaining_seconds" in snap


def test_window_prunes_old_samples():
    """Old samples fall out of the window and stop counting."""
    cb = CircuitBreaker(
        failure_threshold=10, min_samples=10, window_seconds=0.1, cooldown_seconds=100.0
    )
    for _ in range(5):
        cb.record(False)
    time.sleep(0.15)
    cb.record(True)  # triggers prune
    snap = cb.snapshot()
    assert snap["window_samples"] == 1
    assert snap["window_failures"] == 0


def test_successes_dont_prevent_trip_if_failures_meet_threshold():
    """We count failures in the window, not a ratio."""
    cb = CircuitBreaker(failure_threshold=5, min_samples=5, cooldown_seconds=100.0)
    for _ in range(10):
        cb.record(True)
    for _ in range(5):
        cb.record(False)
    assert cb.state() == BreakerState.OPEN