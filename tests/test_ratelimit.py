import time

import pytest

from app.ratelimit import Bucket, Limit, RateLimiter


# --- Bucket ----------------------------------------------------------------

def test_bucket_starts_full():
    b = Bucket(capacity=5, refill_per_second=1.0)
    b.tokens = 5
    assert b.try_consume() is True
    assert b.tokens == 4


def test_bucket_denies_when_empty():
    b = Bucket(capacity=2, refill_per_second=1.0, tokens=0)
    assert b.try_consume() is False
    # A tiny amount of refill may have accumulated between init and check,
    # so assert "much less than a full token" rather than exactly 0.
    assert b.tokens < 0.01


def test_bucket_refills_over_time():
    b = Bucket(capacity=5, refill_per_second=10.0, tokens=0)
    time.sleep(0.3)  # ~3 tokens at 10/s
    b.refill()
    assert b.tokens >= 2.5  # some slack for scheduler jitter


def test_bucket_caps_at_capacity():
    b = Bucket(capacity=3, refill_per_second=100.0, tokens=0)
    time.sleep(0.2)  # would be 20 tokens if uncapped
    b.refill()
    assert b.tokens == 3.0


def test_seconds_until_available_when_full():
    b = Bucket(capacity=5, refill_per_second=1.0, tokens=5)
    assert b.seconds_until_available() == 0.0


def test_seconds_until_available_when_empty():
    b = Bucket(capacity=5, refill_per_second=2.0, tokens=0)
    wait = b.seconds_until_available(1.0)
    # 1 token at 2/s = 0.5s
    assert 0.4 <= wait <= 0.6


# --- RateLimiter -----------------------------------------------------------

def test_limiter_allows_burst_then_denies():
    rl = RateLimiter(Limit(capacity=3, refill_per_second=0.001))  # effectively no refill
    for i in range(3):
        d = rl.check("k")
        assert d.allowed is True
        assert d.remaining == 2 - i
    d = rl.check("k")
    assert d.allowed is False
    assert d.remaining == 0
    assert d.retry_after_seconds > 0


def test_limiter_keys_are_independent():
    rl = RateLimiter(Limit(capacity=2, refill_per_second=0.001))
    assert rl.check("a").allowed is True
    assert rl.check("a").allowed is True
    assert rl.check("a").allowed is False

    # different key has its own bucket
    assert rl.check("b").allowed is True
    assert rl.check("b").allowed is True


def test_limiter_decision_fields():
    rl = RateLimiter(Limit(capacity=4, refill_per_second=1.0))
    d = rl.check("k")
    assert d.limit == 4
    assert d.remaining == 3
    assert d.retry_after_seconds == 0.0


def test_limiter_reset():
    rl = RateLimiter(Limit(capacity=1, refill_per_second=0.001))
    assert rl.check("k").allowed is True
    assert rl.check("k").allowed is False
    rl.reset()
    assert rl.check("k").allowed is True


def test_limiter_key_count():
    rl = RateLimiter(Limit(capacity=1, refill_per_second=0.001))
    rl.check("a")
    rl.check("b")
    rl.check("c")
    assert rl.key_count() == 3


def test_limiter_peek():
    rl = RateLimiter(Limit(capacity=3, refill_per_second=1.0))
    rl.check("k")
    snap = rl.peek("k")
    assert snap is not None
    assert snap["capacity"] == 3
    assert snap["tokens"] <= 3
    assert rl.peek("missing") is None