"""Fetching social mentions and resolving them to issuers.

What gets stored is a matching decision, not an ingestion decision. An item with
no candidate alias at all is not persisted: 99.7% of a real Hacker News window
mentions no issuer, and keeping those would be keeping the internet.

"Unmatched mentions are kept" in the brief means something narrower and more
useful -- an item that *did* produce a candidate but scored below the accept
threshold is stored with needs_review = true, so match precision stays auditable
and the review queue has something to show.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg

from backend.config import Settings
from backend.match.matcher import ACCEPT_THRESHOLD, AliasRow, match
from backend.sources.base import RawMention, SourceAdapter

logger = logging.getLogger(__name__)


@dataclass
class SocialReport:
    fetched: int = 0
    candidates: int = 0
    accepted: int = 0
    needs_review: int = 0
    inserted: int = 0
    duplicates: int = 0
    per_source: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"fetched={self.fetched} candidates={self.candidates} "
            f"accepted={self.accepted} review={self.needs_review} "
            f"inserted={self.inserted} dup={self.duplicates}"
        )


_INSERT = """
    INSERT INTO mentions (source, source_uid, issuer_id, matched_alias_id,
                          match_confidence, needs_review, author_hash, channel,
                          title, body_excerpt, url, engagement_score, posted_at)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
    ON CONFLICT (source, source_uid) DO NOTHING
    RETURNING id
"""


async def _load_aliases(connection: asyncpg.Connection) -> list[AliasRow]:
    rows = await connection.fetch("SELECT id, issuer_id, normalized_alias, kind FROM aliases")
    return [AliasRow(r["id"], r["issuer_id"], r["normalized_alias"], r["kind"]) for r in rows]


async def ingest_social(
    pool: asyncpg.Pool,
    adapters: list[SourceAdapter],
    settings: Settings,
    since: datetime | None = None,
) -> SocialReport:
    report = SocialReport()
    since = since or datetime.now(UTC) - timedelta(days=settings.social_lookback_days)

    async with pool.acquire() as connection:
        aliases = await _load_aliases(connection)
    if not aliases:
        logger.warning("social: no aliases; run alias generation first")
        return report

    for adapter in adapters:
        try:
            items: list[RawMention] = await adapter.fetch(since)
        except Exception:
            # One source failing must not stop the others.
            logger.exception("social: %s fetch failed", adapter.name)
            continue

        report.fetched += len(items)
        report.per_source[adapter.name] = len(items)

        async with pool.acquire() as connection:
            for item in items:
                result = match(aliases, item.title or "", item.body_excerpt or "")
                if result is None:
                    continue

                report.candidates += 1
                if result.needs_review:
                    report.needs_review += 1
                else:
                    report.accepted += 1

                mention_id = await connection.fetchval(
                    _INSERT,
                    item.source, item.source_uid,
                    # issuer_id is set even for review rows: the reviewer needs
                    # to see what was proposed. needs_review is what marks it
                    # as unconfirmed.
                    result.issuer_id, result.alias_id, result.confidence,
                    result.needs_review, item.author_hash, item.channel,
                    item.title, item.body_excerpt, item.url,
                    item.engagement_score, item.posted_at,
                )
                if mention_id is None:
                    report.duplicates += 1
                else:
                    report.inserted += 1

    return report
