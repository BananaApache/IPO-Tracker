"""Hacker News via the Algolia search API. No key, no account.

Fetches the corpus by time window rather than by searching for issuer names.
That matters for Phase 3b: searching "Circle" would return whatever Algolia
thinks matches, and the matcher would then be scored on a set that a different
matcher already filtered. Pulling everything in the window and matching locally
means precision and recall are measured against what was actually published.
"""

import logging
from datetime import UTC, datetime

from backend.config import Settings
from backend.http import RetryingClient
from backend.sources.base import RawMention, hash_author

logger = logging.getLogger(__name__)

_ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"

# Algolia caps page * hitsPerPage at 1000, so `page=1` with a full page size
# returns nothing at all. Deep pagination is done by walking the time window
# backwards instead: take the oldest item of each batch and ask for everything
# older than it.
_PER_PAGE = 1000
_MAX_BATCHES = 60

_EXCERPT_CHARS = 500


class HackerNewsAdapter:
    name = "hn"

    def __init__(self, settings: Settings, client: RetryingClient | None = None) -> None:
        self._salt = settings.mention_hash_salt
        self._max_items = settings.hn_max_items
        self._client = client or RetryingClient(
            user_agent=settings.sec_user_agent,
            # Algolia's HN endpoint is generous; this is politeness, not a
            # published ceiling.
            per_second=5.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, since: datetime) -> list[RawMention]:
        cutoff = int(since.timestamp())
        newest = None
        seen: set[str] = set()
        mentions: list[RawMention] = []

        for _ in range(_MAX_BATCHES):
            window = f"created_at_i>{cutoff}"
            if newest is not None:
                window += f",created_at_i<{newest}"

            payload = await self._client.get_json(
                _ENDPOINT,
                params={
                    "tags": "(story,comment)",
                    "numericFilters": window,
                    "hitsPerPage": _PER_PAGE,
                },
            )
            hits = payload.get("hits", [])
            if not hits:
                break

            oldest = min(int(h["created_at_i"]) for h in hits if h.get("created_at_i"))
            for hit in hits:
                mention = self._to_mention(hit)
                # Batches overlap on the boundary second, so dedupe by id.
                if mention is not None and mention.source_uid not in seen:
                    seen.add(mention.source_uid)
                    mentions.append(mention)

            if len(mentions) >= self._max_items or oldest <= cutoff:
                break
            # +1 so an item exactly on the boundary is not skipped.
            newest = oldest + 1

        logger.info("hn: %d items since %s", len(mentions), since.date())
        return mentions[: self._max_items]

    def _to_mention(self, hit: dict) -> RawMention | None:
        object_id = hit.get("objectID")
        created = hit.get("created_at")
        if not object_id or not created:
            return None

        # A comment carries its parent's title in story_title; its own text is
        # comment_text. Stories have title and no comment_text.
        title = hit.get("title") or hit.get("story_title")
        body = hit.get("comment_text") or hit.get("story_text")

        return RawMention(
            source=self.name,
            source_uid=str(object_id),
            posted_at=datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(UTC),
            # Hashed here, in the adapter. The raw handle never leaves this
            # function, and RawMention has nowhere to put one.
            author_hash=hash_author(hit.get("author"), self._salt),
            channel="news.ycombinator.com",
            title=title,
            body_excerpt=body[:_EXCERPT_CHARS] if body else None,
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
            # Points and comment count are different signals but both are
            # attention; the rollup weights them together anyway.
            engagement_score=int(hit.get("points") or 0) + int(hit.get("num_comments") or 0),
        )
