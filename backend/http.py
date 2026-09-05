"""Shared outbound HTTP: rate limiting and retry.

One limiter per source, not one global limiter. Every source publishes its own
ceiling and they are wildly different -- SEC allows 10 requests/second, GDELT
allows one every five -- so a single shared limit would either violate the
strictest or waste the most generous.
"""

import asyncio
import logging
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Spaces outbound requests to at most `per_second`.

    A spacing lock rather than a token bucket. A bucket permits a burst up to
    its capacity, and a burst is what trips a throttle even when the average
    rate is legal.
    """

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / per_second
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        # Held across the sleep on purpose: that is what serialises concurrent
        # callers instead of letting them all wake at once.
        async with self._lock:
            now = loop.time()
            delay = self._next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_allowed = now + self._min_interval


class HttpError(RuntimeError):
    """A request failed after exhausting retries."""


class RetryingClient:
    def __init__(
        self,
        *,
        user_agent: str,
        per_second: float,
        max_retries: int = 5,
        timeout: float = 30.0,
        base_backoff: float = 2.0,
        max_backoff: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.limiter = RateLimiter(per_second)
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def inspect(self, response: httpx.Response) -> None:
        """Hook for source-specific checks. Raise to fail fast, return to accept.

        Exists because "this request failed" is not always visible in the status
        code -- see SecClient, where a rejected User-Agent arrives as a 403 with
        an HTML page.
        """

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            await self.limiter.acquire()
            try:
                response = await self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                await self._backoff(attempt, None, f"transport error: {exc!r}", url)
                continue

            self.inspect(response)

            if response.status_code in RETRYABLE_STATUS:
                last_error = httpx.HTTPStatusError(
                    str(response.status_code), request=response.request, response=response
                )
                await self._backoff(
                    attempt, response.headers.get("Retry-After"),
                    f"HTTP {response.status_code}", url,
                )
                continue

            response.raise_for_status()
            return response

        raise HttpError(f"giving up on {url} after {self.max_retries} attempts") from last_error

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return (await self.get(url, params)).json()

    async def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        return (await self.get(url, params)).text

    async def _backoff(self, attempt: int, retry_after: str | None, reason: str, url: str) -> None:
        # base_backoff is a MULTIPLIER on a power of two, not the exponent
        # base. Writing this as `base ** attempt` looks equivalent and is not:
        # with GDELT's base of 6 it produced waits of 6s, 36s, 216s and 1296s,
        # and a single throttled request hung for over twenty minutes.
        computed = min(self.base_backoff * (2**attempt), self.max_backoff)

        if retry_after is not None:
            try:
                # Honour the server's own number when it gives one. Guessing
                # shorter is what turns throttling into a ban.
                delay = min(float(retry_after), self.max_backoff)
            except ValueError:
                delay = computed
        else:
            delay = computed

        # Jitter, so a restart does not resynchronise every retry into one instant.
        delay += random.uniform(0, 0.5)
        logger.warning("http: %s for %s; retrying in %.1fs", reason, url, delay)
        await asyncio.sleep(delay)
