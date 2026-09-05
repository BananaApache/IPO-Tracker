"""News via GDELT's DOC 2.0 API. No key, no account.

Two behaviours worth knowing before reading the code:

* The published limit is **one request every five seconds** -- 25x stricter than
  SEC. per_second is set to 0.2 accordingly.
* When throttled, GDELT returns a plain-text sentence rather than JSON. The
  first probe of this API returned that body with a 200, which `.json()` would
  turn into a parse error that reads like a bug in our code rather than a rate
  limit. `inspect()` below converts it into a visible failure.

Unlike Hacker News, GDELT requires a query -- there is no "everything recently"
mode. At one request per five seconds, per-issuer queries are not viable (62
issuers would take five minutes), so one broad IPO-related query is issued and
matching happens locally. That also keeps the matcher honest for the same reason
as the HN adapter.
"""

import logging
from datetime import UTC, datetime

import httpx

from backend.config import Settings
from backend.http import RetryingClient
from backend.sources.base import RawMention

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_RECORDS = 250  # API ceiling
_THROTTLE_MARKER = "Please limit requests"


class _GdeltClient(RetryingClient):
    def inspect(self, response: httpx.Response) -> None:
        if response.status_code == 200 and response.text.lstrip().startswith(_THROTTLE_MARKER):
            # A 200 that is actually a refusal. Raising keeps it from being
            # parsed as "no articles found".
            raise httpx.HTTPStatusError(
                "GDELT throttled the request (200 with a plain-text refusal)",
                request=response.request,
                response=response,
            )


class GdeltAdapter:
    name = "gdelt"

    def __init__(self, settings: Settings, client: RetryingClient | None = None) -> None:
        self._query = settings.gdelt_query
        self._client = client or _GdeltClient(
            user_agent=settings.sec_user_agent,
            per_second=0.2,      # one request per five seconds, as published
            max_retries=4,
            base_backoff=6.0,    # first retry waits longer than the window itself
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, since: datetime) -> list[RawMention]:
        payload = await self._client.get_json(
            _ENDPOINT,
            params={
                "query": self._query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": _MAX_RECORDS,
                "startdatetime": since.astimezone(UTC).strftime("%Y%m%d%H%M%S"),
                "enddatetime": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
            },
        )

        mentions: list[RawMention] = []
        for article in payload.get("articles", []):
            mention = self._to_mention(article)
            if mention is not None:
                mentions.append(mention)

        logger.info("gdelt: %d articles since %s", len(mentions), since.date())
        return mentions

    def _to_mention(self, article: dict) -> RawMention | None:
        url = article.get("url")
        seen = article.get("seendate")
        if not url or not seen:
            return None
        try:
            posted = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            return None

        return RawMention(
            source=self.name,
            # GDELT has no article id, and the URL is what it deduplicates on.
            source_uid=url,
            posted_at=posted,
            # News articles have bylines, but GDELT does not return them, and
            # this project has no use for one. Nothing to hash.
            author_hash=None,
            channel=article.get("domain"),
            title=article.get("title"),
            body_excerpt=None,   # DOC 2.0 artlist returns no body text
            url=url,
            # No engagement signal from GDELT. Left at 0 rather than invented:
            # the rollup must be able to tell "no engagement data" from "zero
            # engagement".
            engagement_score=0,
        )
