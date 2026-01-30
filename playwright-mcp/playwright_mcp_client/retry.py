from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    delay_seconds: float = 0.5
    backoff_factor: float = 2.0
    max_delay_seconds: float = 5.0


async def async_retry(
    func: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Optional[Callable[[int, BaseException], None]] = None,
) -> T:
    last_error: Optional[BaseException] = None
    delay = policy.delay_seconds

    for attempt in range(1, policy.attempts + 1):
        try:
            return await func()
        except retry_on as exc:  # type: ignore[misc]
            last_error = exc
            if attempt >= policy.attempts:
                break
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(delay)
            delay = min(delay * policy.backoff_factor, policy.max_delay_seconds)

    assert last_error is not None
    raise last_error