from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimiter:
    """Simple minimum-interval limiter for Gmail API quota protection."""

    def __init__(self, min_interval: float = 0.25) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self.min_interval - (now - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def backoff_seconds(attempt: int, *, base: float = 1.0, cap: float = 60.0, jitter: bool = True) -> float:
    attempt = max(0, attempt)
    delay = min(cap, base * (2 ** attempt))
    if jitter:
        delay = delay * (0.5 + random.random() * 0.5)
    return delay


def is_retryable_error(exc: BaseException) -> bool:
    resp = getattr(exc, "resp", None)
    try:
        status = int(getattr(resp, "status", 0) or 0)
    except (TypeError, ValueError):
        status = 0
    if status:
        return status == 429 or status >= 500
    return True


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base: float = 1.0,
    cap: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleeper: Callable[[float], None] = time.sleep,
    should_retry: Callable[[BaseException], bool] | None = None,
) -> T:
    last_error: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            last_error = exc
            if should_retry is not None and not should_retry(exc):
                raise
            if i >= attempts - 1:
                break
            sleeper(backoff_seconds(i, base=base, cap=cap))
    assert last_error is not None
    raise last_error
