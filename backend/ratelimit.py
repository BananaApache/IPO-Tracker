"""Per-client rate limiting for the public API.

Deliberately small. This protects the database and the host from an accidental
loop, not from a determined attacker -- for that the answer is a CDN or a
gateway, not application code.

Note what it does NOT protect: Polygon spend. The API never calls Polygon. All
market-data requests come from the worker process, whose own limiter caps it at
the free tier's 5 requests/minute regardless of public traffic. Nothing a
visitor can do to this API costs money at the data provider.
"""

import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Paths a load balancer or uptime check hits constantly; excluded so a
# health probe cannot exhaust a client's budget.
_EXEMPT = frozenset({"/health"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limit, keyed on client IP.

    A sliding window rather than a fixed one: a fixed window lets a client
    spend its whole budget in the last second of one window and again in the
    first second of the next, which is twice the intended rate at the boundary.
    """

    def __init__(self, app, *, limit: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def _client(self, request: Request) -> str:
        # Behind a platform proxy the socket address is the proxy, so trust the
        # first hop in X-Forwarded-For. Spoofable, which is acceptable: the
        # point is stopping a runaway script, not adversarial abuse.
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT:
            return await call_next(request)

        key = self._client(request)
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())

        while hits and now - hits[0] > self._window:
            hits.popleft()

        if len(hits) >= self._limit:
            retry_after = max(1, int(self._window - (now - hits[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": f"rate limit exceeded: {self._limit} requests per "
                                   f"{self._window}s"},
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)

        # Bound memory. Without this, one request per unique spoofed IP grows
        # the dict forever -- a slow leak that only shows up under scanning.
        if len(self._hits) > 10_000:
            for stale_key in [k for k, v in self._hits.items() if not v or now - v[-1] > self._window]:
                del self._hits[stale_key]

        return await call_next(request)
