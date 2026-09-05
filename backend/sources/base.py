"""The one shape every social source is reduced to.

Everything downstream -- matching, the review queue, the rollup, the scores --
sees `RawMention` and never a platform-specific payload. That is what makes
Reddit a drop-in if API access is granted, and what stops a platform's schema
leaking into the scoring code.

The interface is deliberately one method. A source this project consumes is
read-only by construction: there is nowhere in `SourceAdapter` to express
posting, voting, or messaging, so the read-only guarantee in PROJECT_BRIEF.md
section 7 is enforced by the type rather than by discipline.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RawMention:
    """One post, comment or article, platform-neutral.

    Note what is absent: no username, no user id, no profile URL. Authors exist
    here only as `author_hash`, and adapters are responsible for hashing before
    constructing this object -- there is no field for a raw handle to sit in
    even briefly.
    """

    source: str          # matches SourceAdapter.name and the CHECK on mentions.source
    source_uid: str      # the platform's own id; unique per source
    posted_at: datetime  # timezone-aware, always UTC
    author_hash: str | None
    channel: str | None      # subreddit, HN board, publication
    title: str | None
    body_excerpt: str | None
    url: str | None
    engagement_score: int = 0


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    async def fetch(self, since: datetime) -> list[RawMention]:
        """Everything this source published at or after `since`."""
        ...


def hash_author(handle: str | None, salt: str) -> str | None:
    """Salted SHA-256 of a platform username. The handle is never stored.

    HMAC rather than a plain salt+digest: with `sha256(salt + handle)` an
    attacker who learns the salt can build a rainbow table over a dictionary of
    known usernames, and username dictionaries are cheap. HMAC is the
    keyed-hash construction meant for exactly this.

    The only supported use of the result is counting *distinct* authors. It is
    not a user identifier to join on, profile, or carry across sources.
    """
    if not handle:
        return None
    return hmac.new(salt.encode(), handle.strip().lower().encode(), hashlib.sha256).hexdigest()
