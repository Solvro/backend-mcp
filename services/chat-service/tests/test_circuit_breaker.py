import pytest
from chat_app.mcp_gateway import CircuitBreaker, CircuitState

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_breaker(clock: FakeClock, threshold: int = 3, reset: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker(
        failure_threshold=threshold,
        reset_timeout=reset,
        time_func=clock,
    )


def test_starts_closed_and_allows() -> None:
    breaker = make_breaker(FakeClock())
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_opens_after_threshold_consecutive_failures() -> None:
    breaker = make_breaker(FakeClock(), threshold=3)

    for _ in range(2):
        breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED  # not yet

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False  # fail fast


def test_success_resets_failure_count() -> None:
    breaker = make_breaker(FakeClock(), threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()  # counter back to 0

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_after_cooldown_then_close_on_success() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1, reset=30.0)

    breaker.record_failure()  # trips immediately (threshold=1)
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False

    clock.advance(30.0)
    assert breaker.allow() is True  # single probe -> HALF_OPEN
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is False  # only one probe permitted at a time

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.allow() is True


def test_half_open_failure_reopens_and_restarts_cooldown() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1, reset=30.0)

    breaker.record_failure()
    clock.advance(30.0)
    assert breaker.allow() is True  # HALF_OPEN probe

    breaker.record_failure()  # probe failed
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False  # cooldown restarted, not immediately open

    clock.advance(30.0)
    assert breaker.allow() is True


def test_retry_after_counts_down() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock, threshold=1, reset=30.0)

    breaker.record_failure()
    assert breaker.retry_after() == pytest.approx(30.0)

    clock.advance(10.0)
    assert breaker.retry_after() == pytest.approx(20.0)

    clock.advance(25.0)
    assert breaker.retry_after() == 0.0  # elapsed
