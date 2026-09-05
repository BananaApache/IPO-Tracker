"""The only place this project talks to sec.gov.

Every SEC request goes through `SecClient` so that the rate limit, the
identifying User-Agent, and the retry policy exist once. Scattering any of the
three is how a project ends up quietly banned: the limit is per *requester*, not
per code path, so two modules each politely doing 6 req/s is 12 req/s to the SEC.
"""

import asyncio
import logging
import random
from typing import Any

import httpx

from backend.config import Settings

logger = logging.getLogger(__name__)

# SEC returns this page instead of the file when it does not like a request.
# It arrives as a 403, which is the same status a missing file returns, so the
# body is the only way to tell "your User-Agent is wrong" from "slow down".
_UNDECLARED_TOOL_MARKER = "Undeclared Automated Tool"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SecMisconfiguredError(RuntimeError):
    """The User-Agent was rejected. Retrying cannot fix this."""


class SecRequestError(RuntimeError):
    """A request failed after exhausting retries."""


class RateLimiter:
    """Spaces outbound requests to at most `per_second`.

    Deliberately a spacing lock rather than a token bucket: a bucket permits a
    burst up to its capacity, and a burst is exactly what trips SEC's throttle
    even when the average rate is legal.
    """

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / per_second
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        # The lock is held across the sleep on purpose: that is what serialises
        # concurrent callers instead of letting them all wake at once.
        async with self._lock:
            now = loop.time()
            delay = self._next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_allowed = now + self._min_interval


class SecClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._limiter = RateLimiter(settings.sec_rate_limit_per_second)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            headers={
                # SEC requires a descriptive agent with a real contact address.
                # Without it every request 403s, and the response is an HTML
                # page rather than an error, so it reads like a parsing bug.
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        )

    async def __aenter__(self) -> "SecClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, url: str) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(self._settings.sec_max_retries):
            await self._limiter.acquire()
            try:
                response = await self._client.get(url)
            except httpx.TransportError as exc:
                last_error = exc
                await self._sleep_before_retry(attempt, None, f"transport error: {exc!r}", url)
                continue

            if response.status_code == 403 and _UNDECLARED_TOOL_MARKER in response.text:
                raise SecMisconfiguredError(
                    f"SEC rejected the User-Agent {self._settings.sec_user_agent!r}. "
                    "Set SEC_USER_AGENT to a descriptive value containing a real "
                    "contact email."
                )

            if response.status_code in _RETRYABLE_STATUS:
                last_error = httpx.HTTPStatusError(
                    f"{response.status_code}", request=response.request, response=response
                )
                await self._sleep_before_retry(
                    attempt, response.headers.get("Retry-After"),
                    f"HTTP {response.status_code}", url,
                )
                continue

            response.raise_for_status()
            return response

        raise SecRequestError(
            f"giving up on {url} after {self._settings.sec_max_retries} attempts"
        ) from last_error

    async def get_text(self, url: str) -> str:
        return (await self.get(url)).text

    async def get_json(self, url: str) -> Any:
        return (await self.get(url)).json()

    async def _sleep_before_retry(
        self, attempt: int, retry_after: str | None, reason: str, url: str
    ) -> None:
        if retry_after is not None:
            try:
                # Honour the server's own number when it gives one; guessing
                # shorter is what turns throttling into a ban.
                delay = float(retry_after)
            except ValueError:
                delay = 2.0**attempt
        else:
            delay = 2.0**attempt

        # Jitter so that a worker restart does not resynchronise every retry
        # into the same instant.
        delay += random.uniform(0, 0.5)
        logger.warning("sec: %s for %s; retrying in %.1fs", reason, url, delay)
        await asyncio.sleep(delay)
