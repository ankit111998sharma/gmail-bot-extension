from __future__ import annotations

import time

from gmail_bot.resilience import RateLimiter, backoff_seconds, retry_call


def test_backoff_doubles_without_jitter() -> None:
    assert backoff_seconds(0, base=1, cap=100, jitter=False) == 1
    assert backoff_seconds(1, base=1, cap=100, jitter=False) == 2
    assert backoff_seconds(2, base=1, cap=100, jitter=False) == 4
    assert backoff_seconds(10, base=1, cap=8, jitter=False) == 8


def test_retry_call_retries_then_succeeds() -> None:
    state = {"n": 0}

    def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise ConnectionError("down")
        return "ok"

    assert retry_call(flaky, attempts=4, base=0.01, cap=0.05, retry_on=(ConnectionError,), sleeper=lambda _: None) == "ok"
    assert state["n"] == 3


def test_rate_limiter_enforces_interval() -> None:
    limiter = RateLimiter(0.05)
    t0 = time.monotonic()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.04
