import time
from collections.abc import Callable
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_timeout: float,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._reset_timeout = reset_timeout
        self._now = time_func

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        return self._state

    def retry_after(self) -> float:
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            return max(0.0, self._reset_timeout - (self._now() - self._opened_at))
        return 0.0

    def allow(self) -> bool:
        if self._state is CircuitState.CLOSED:
            return True

        if self._state is CircuitState.OPEN:
            if self.retry_after() <= 0.0:
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = True
                return True
            return False

        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        if self._state is CircuitState.HALF_OPEN:
            self._trip()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._trip()

    def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._now()
