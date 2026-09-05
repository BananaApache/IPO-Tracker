"""The only place this project talks to sec.gov.

Every SEC request goes through `SecClient` so the rate limit, the identifying
User-Agent and the retry policy exist once. Scattering any of the three is how a
project ends up quietly banned: the limit is per *requester*, not per code path,
so two modules each politely doing 6 req/s is 12 req/s to the SEC.

Rate limiting and backoff live in backend/http.py, shared with the social source
adapters. What is SEC-specific is below.
"""

import httpx

from backend.config import Settings
from backend.http import HttpError, RetryingClient

# SEC returns this page instead of the file when it does not like a request. It
# arrives as a 403, the same status a missing file returns, so the body is the
# only way to tell "your User-Agent is wrong" from "slow down".
_UNDECLARED_TOOL_MARKER = "Undeclared Automated Tool"


class SecMisconfiguredError(RuntimeError):
    """The User-Agent was rejected. Retrying cannot fix this."""


# Kept as an alias so existing imports and error handling continue to work.
SecRequestError = HttpError


class SecClient(RetryingClient):
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(
            user_agent=settings.sec_user_agent,
            per_second=settings.sec_rate_limit_per_second,
            max_retries=settings.sec_max_retries,
            client=client,
        )
        self._user_agent = settings.sec_user_agent

    def inspect(self, response: httpx.Response) -> None:
        if response.status_code == 403 and _UNDECLARED_TOOL_MARKER in response.text:
            raise SecMisconfiguredError(
                f"SEC rejected the User-Agent {self._user_agent!r}. Set SEC_USER_AGENT "
                "to a descriptive value containing a real contact email."
            )
