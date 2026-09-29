"""Circuit breaker for an upstream instance.

Three states:

  CLOSED     normal operation; traffic flows, outcomes are recorded
  OPEN       tripped; all traffic is blocked until the cooldown elapses
  HALF_OPEN  cooldown elapsed; one trial request is allowed through
             - success -> CLOSED
             - failure -> OPEN (reset cooldown)

Trips when the number of failures within a rolling window exceeds
`failure_threshold`. A minimum number of samples (`min_samples`) must be
observed before the breaker can trip, so a single failure doesn't shut down
a healthy upstream.
"""

import threading
import time
from collections import deque
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: float = 10.0,
        cooldown_seconds: float = 15.0,
        min_samples: int = 5,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.min_samples = min_samples

        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        # deque of (timestamp, ok: bool), oldest first
        self._outcomes: deque[tuple[float, bool]] = deque()
        self._opened_at: float = 0.0
        self._half_open_probe_in_flight: bool = False

    # --- Public API -------------------------------------------------------

    def state(self) -> BreakerState:
        """Current state. May transition OPEN -> HALF_OPEN on read."""
        with self._lock:
            self._maybe_half_open()
            return self._state

    def allow_request(self) -> bool:
        """Whether a request should be allowed through right now."""
        with self._lock:
            self._maybe_half_open()
            if self._state == BreakerState.CLOSED:
                return True
            if self._state == BreakerState.OPEN:
                return False
            # HALF_OPEN: allow exactly one probe at a time
            if self._half_open_probe_in_flight:
                return False
            self._half_open_probe_in_flight = True
            return True

    def record(self, ok: bool) -> None:
        """Record the outcome of a request that was allowed through."""
        with self._lock:
            now = time.monotonic()
            if self._state == BreakerState.HALF_OPEN:
                # Whatever the probe did decides the next state
                self._half_open_probe_in_flight = False
                if ok:
                    self._reset_locked()
                else:
                    self._open_locked(now)
                return

            # CLOSED (or OPEN shouldn't record, but be defensive)
            self._outcomes.append((now, ok))
            self._prune_locked(now)

            if not ok:
                failures = sum(1 for _, was_ok in self._outcomes if not was_ok)
                total = len(self._outcomes)
                if total >= self.min_samples and failures >= self.failure_threshold:
                    self._open_locked(now)

    def snapshot(self) -> dict:
        with self._lock:
            self._maybe_half_open()
            now = time.monotonic()
            self._prune_locked(now)
            recent = list(self._outcomes)
            failures = sum(1 for _, was_ok in recent if not was_ok)
            cooldown_remaining = (
                max(0.0, self.cooldown_seconds - (now - self._opened_at))
                if self._state == BreakerState.OPEN else 0.0
            )
            return {
                "state": self._state.value,
                "window_samples": len(recent),
                "window_failures": failures,
                "failure_threshold": self.failure_threshold,
                "cooldown_remaining_seconds": round(cooldown_remaining, 2),
            }

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def force_open(self) -> None:
        """For tests: trip the breaker immediately."""
        with self._lock:
            self._open_locked(time.monotonic())

    # --- Internals --------------------------------------------------------

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._outcomes and self._outcomes[0][0] < cutoff:
            self._outcomes.popleft()

    def _maybe_half_open(self) -> None:
        if self._state != BreakerState.OPEN:
            return
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_probe_in_flight = False

    def _open_locked(self, now: float) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = now
        self._half_open_probe_in_flight = False
        self._outcomes.clear()

    def _reset_locked(self) -> None:
        self._state = BreakerState.CLOSED
        self._outcomes.clear()
        self._half_open_probe_in_flight = False
        self._opened_at = 0.0