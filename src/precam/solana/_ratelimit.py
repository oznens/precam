"""Tiny async token-bucket rate limiter.

Used as a process-global gate in front of external APIs (Helius, Geckoterminal)
so that bursty callers (multi-wallet refresh, discover loops) never exceed the
provider's per-second cap.

Usage:
    limiter = AsyncRateLimiter(per_second=8.0)
    async with limiter:
        await http_call()
"""

import asyncio
import time


class AsyncRateLimiter:
    """One token per call, capacity == per_second (1 second of burst headroom)."""

    def __init__(self, per_second: float):
        if per_second <= 0:
            raise ValueError("per_second must be > 0")
        self.per_second = per_second
        self.capacity = float(per_second)
        self.tokens = float(per_second)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self._last) * self.per_second,
            )
            self._last = now
            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.per_second
                await asyncio.sleep(wait)
                self.tokens = 1.0
                self._last = time.monotonic()
            self.tokens -= 1.0

    async def __aenter__(self) -> "AsyncRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False
