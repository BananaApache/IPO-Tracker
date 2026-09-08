"""Reddit via OAuth.

**This adapter never touches an unauthenticated endpoint.** `reddit.com/*.json`
is prohibited by `PROJECT_BRIEF.md` §7 and would contradict the API access
request this project has filed; it also 403s from datacenter IPs, so it would
pass locally and fail in deployment. Everything here goes through
`oauth.reddit.com` with a bearer token.

The adapter is only registered when both credentials are configured, so an
unconfigured deployment makes no request to Reddit at all.

Like the Hacker News adapter, this pulls a listing and matches locally rather
than searching per issuer name — searching would hand the matcher a set that
Reddit's own relevance ranking had already filtered, and precision measured on
that would mean nothing.
"""

import logging
from datetime import UTC, datetime

import httpx

from backend.config import Settings
from backend.http import RetryingClient
from backend.sources.base import RawMention, hash_author

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_LISTING = "https://oauth.reddit.com/r/{subreddits}/new"

_PAGE_SIZE = 100      # Reddit's per-request maximum
_MAX_PAGES = 10       # listings are capped around 1,000 items regardless
_EXCERPT_CHARS = 500


class RedditAdapter:
    name = "reddit"

    def __init__(self, settings: Settings, client: RetryingClient | None = None) -> None:
        if not (settings.reddit_client_id and settings.reddit_client_secret):
            raise RuntimeError("Reddit credentials are not configured")
        self._settings = settings
        self._salt = settings.mention_hash_salt
        self._token: str | None = None
        self._client = client or RetryingClient(
            user_agent=settings.reddit_user_agent or settings.sec_user_agent,
            per_second=settings.reddit_rate_limit_per_second,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _bearer(self) -> str:
        """App-only token via client_credentials. Read-only by construction:
        this grant has no user context, so it cannot post, vote or message."""
        if self._token:
            return self._token
        async with httpx.AsyncClient(timeout=30) as raw:
            response = await raw.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self._settings.reddit_client_id, self._settings.reddit_client_secret),
                headers={"User-Agent": self._settings.reddit_user_agent
                                       or self._settings.sec_user_agent},
            )
        response.raise_for_status()
        self._token = response.json()["access_token"]
        return self._token

    async def fetch(
        self, since: datetime, until: datetime | None = None
    ) -> list[RawMention]:
        token = await self._bearer()
        cutoff = since.timestamp()
        ceiling = until.timestamp() if until else None

        mentions: list[RawMention] = []
        after: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, object] = {"limit": _PAGE_SIZE}
            if after:
                params["after"] = after
            response = await self._client.get(
                _LISTING.format(subreddits=self._settings.reddit_subreddits),
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            payload = response.json()
            children = payload.get("data", {}).get("children", [])
            if not children:
                break

            oldest = None
            for child in children:
                post = child.get("data") or {}
                created = post.get("created_utc")
                if created is None:
                    continue
                oldest = created if oldest is None else min(oldest, created)
                if created < cutoff or (ceiling and created > ceiling):
                    continue
                mention = self._to_mention(post)
                if mention is not None:
                    mentions.append(mention)

            after = payload.get("data", {}).get("after")
            if not after or (oldest is not None and oldest < cutoff):
                break

        logger.info("reddit: %d posts since %s", len(mentions), since.date())
        return mentions

    def _to_mention(self, post: dict) -> RawMention | None:
        post_id = post.get("id")
        created = post.get("created_utc")
        if not post_id or created is None:
            return None

        body = post.get("selftext") or None
        return RawMention(
            source=self.name,
            source_uid=str(post_id),
            posted_at=datetime.fromtimestamp(float(created), tz=UTC),
            # Hashed here. The username never leaves this function, and
            # RawMention has no field one could sit in.
            author_hash=hash_author(post.get("author"), self._salt),
            channel=post.get("subreddit"),
            title=post.get("title"),
            body_excerpt=body[:_EXCERPT_CHARS] if body else None,
            url=f"https://www.reddit.com{post.get('permalink', '')}" or None,
            engagement_score=int(post.get("score") or 0) + int(post.get("num_comments") or 0),
        )
