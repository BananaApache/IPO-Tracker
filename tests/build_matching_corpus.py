"""Builds the labelling corpus for entity resolution.

Streams a long Hacker News window and keeps only what is worth labelling:

  * every item the matcher would STORE (score >= REVIEW_THRESHOLD) -- the exact
    precision denominator;
  * a reservoir sample of items that contain an alias token but score below the
    threshold -- to estimate what scoring rejects;
  * a reservoir sample of items containing no alias token at all -- because that
    is what almost all real traffic looks like.

Everything else is counted and discarded. Holding 90 days of Hacker News in
memory is not necessary and not possible.

    uv run python -m tests.build_matching_corpus 90
"""

import asyncio
import json
import pathlib
import random
import re
import sys
from datetime import UTC, datetime, timedelta

import asyncpg

from backend.config import Settings, get_settings
from backend.match.matcher import REVIEW_THRESHOLD, AliasRow, match, normalize_text
from backend.sources.hackernews import HackerNewsAdapter

OUT = pathlib.Path(
    "/private/tmp/claude-501/-Users-daniel-Documents-coding-stuff-IPOTracker/"
    "1fbd3569-2463-4bbf-a251-86351352c9e6/scratchpad/hn90"
)
SUBTHRESHOLD_SAMPLE = 400
NOISE_SAMPLE = 300


async def load_aliases() -> list[AliasRow]:
    connection = await asyncpg.connect(get_settings().database_dsn, statement_cache_size=0)
    rows = await connection.fetch("SELECT id, issuer_id, normalized_alias, kind FROM aliases")
    await connection.close()
    return [AliasRow(r["id"], r["issuer_id"], r["normalized_alias"], r["kind"]) for r in rows]


def build_index(aliases: list[AliasRow]) -> tuple[dict[str, list[AliasRow]], int]:
    """alias token -> rows, plus the longest alias in tokens.

    Lets an item be screened in O(its own length) instead of O(number of
    aliases). At 1,731 aliases and ~900k items the naive loop is 1.5 billion
    regex evaluations; this is a dict lookup per n-gram.
    """
    index: dict[str, list[AliasRow]] = {}
    for alias in aliases:
        index.setdefault(alias.normalized_alias, []).append(alias)
    longest = max((len(a.normalized_alias.split()) for a in aliases), default=1)
    return index, longest


def candidates_for(text: str, index: dict[str, list[AliasRow]], longest: int) -> list[AliasRow]:
    tokens = normalize_text(text).split()
    found: list[AliasRow] = []
    for size in range(1, longest + 1):
        for start in range(len(tokens) - size + 1):
            gram = " ".join(tokens[start : start + size])
            if gram in index:
                found.extend(index[gram])
    return found


async def main(days: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    aliases = await load_aliases()
    index, longest = build_index(aliases)
    print(f"{len(aliases)} aliases, longest {longest} tokens")

    rng = random.Random(20260906)
    settings = Settings(hn_max_items=10_000_000)
    adapter = HackerNewsAdapter(settings)

    total = 0
    stored: list[dict] = []
    sub: list[dict] = []
    noise: list[dict] = []

    def reservoir(pool: list[dict], item: dict, cap: int, seen: int) -> None:
        if len(pool) < cap:
            pool.append(item)
        else:
            j = rng.randrange(seen)
            if j < cap:
                pool[j] = item

    seen_sub = seen_noise = 0
    since = datetime.now(UTC) - timedelta(days=days)
    # Walk the window in slices so a failure loses one slice, not the run.
    slice_days = 2
    cursor = datetime.now(UTC)
    try:
        while cursor > since:
            slice_start = max(since, cursor - timedelta(days=slice_days))
            batch = await adapter.fetch(slice_start, until=cursor)
            for m in batch:
                total += 1
                text = f"{m.title or ''} {m.body_excerpt or ''}"
                hits = candidates_for(text, index, longest)
                row = {
                    "source_uid": m.source_uid, "title": m.title,
                    "body_excerpt": m.body_excerpt, "url": m.url,
                    "posted_at": m.posted_at.isoformat(),
                    "author_hash": m.author_hash, "channel": m.channel,
                    "engagement_score": m.engagement_score,
                    "alias_hits": sorted({a.normalized_alias for a in hits}),
                }
                if not hits:
                    seen_noise += 1
                    reservoir(noise, row, NOISE_SAMPLE, seen_noise)
                    continue
                result = match(hits, m.title or "", m.body_excerpt or "")
                if result is not None:
                    row["score"] = result.confidence
                    row["needs_review"] = result.needs_review
                    row["issuer_id"] = result.issuer_id
                    row["reasons"] = list(result.reasons)
                    stored.append(row)
                else:
                    seen_sub += 1
                    reservoir(sub, row, SUBTHRESHOLD_SAMPLE, seen_sub)
            cursor = slice_start
            print(f"  ...{cursor:%Y-%m-%d}  total={total:,} stored={len(stored)} "
                  f"sub={seen_sub:,} noise={seen_noise:,}", flush=True)
    finally:
        await adapter.aclose()

    payload = {
        "days": days, "total_items": total,
        "stored": stored,
        "subthreshold_sample": sub, "subthreshold_total": seen_sub,
        "noise_sample": noise, "noise_total": seen_noise,
    }
    (OUT / "corpus.json").write_text(json.dumps(payload))
    print(f"\ntotal={total:,}  would-store={len(stored)}  "
          f"alias-hit-but-rejected={seen_sub:,}  no-alias={seen_noise:,}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 90))
