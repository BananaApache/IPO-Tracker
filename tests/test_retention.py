"""The retention sweep deletes rows, so it is pinned down on a seeded fixture.

Every case runs inside a transaction that is rolled back, so the suite can be
pointed at a development database without destroying anything.

    uv run pytest tests/test_retention.py -v
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import asyncpg
import pytest

from backend.config import get_settings
from backend.ingest.retention import sweep_mentions

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
RETENTION_DAYS = 90


def run(coro):
    return asyncio.run(coro)


async def _seed(connection: asyncpg.Connection) -> dict[str, int]:
    """Two issuers and mentions straddling the cutoff. Returns mention ids."""
    kept_issuer = await connection.fetchval(
        "INSERT INTO issuers (cik, legal_name, normalized_name) "
        "VALUES ('9900000001','Retention Fixture A','retention fixture a') RETURNING id"
    )
    other_issuer = await connection.fetchval(
        "INSERT INTO issuers (cik, legal_name, normalized_name) "
        "VALUES ('9900000002','Retention Fixture B','retention fixture b') RETURNING id"
    )

    async def mention(uid: str, issuer_id: int | None, days_ago: int) -> int:
        posted = NOW - timedelta(days=days_ago)
        return await connection.fetchval(
            "INSERT INTO mentions (source, source_uid, issuer_id, author_hash, posted_at) "
            "VALUES ('hn', $1, $2, 'deadbeef', $3) RETURNING id",
            uid, issuer_id, posted,
        )

    ids = {
        # expired and rolled up -> must go
        "old_aggregated": await mention("old-agg", kept_issuer, 200),
        # expired, matched, NOT rolled up -> must be withheld
        "old_unaggregated": await mention("old-unagg", other_issuer, 200),
        # expired and unmatched -> nothing can ever aggregate it, so it goes
        "old_unmatched": await mention("old-unmatched", None, 200),
        # one day inside the window -> stays
        "edge_inside": await mention("edge-in", kept_issuer, RETENTION_DAYS - 1),
        # recent -> stays
        "recent": await mention("recent", kept_issuer, 1),
    }

    # Aggregate only the first issuer's old day.
    await connection.execute(
        "INSERT INTO mention_daily (issuer_id, day, source, mention_count, unique_authors, "
        "weighted_engagement) VALUES ($1, $2, 'hn', 1, 1, 5)",
        kept_issuer, (NOW - timedelta(days=200)).date(),
    )
    ids["kept_issuer"] = kept_issuer
    return ids


async def _with_fixture(body):
    """Seed, run `body`, roll everything back."""
    connection = await asyncpg.connect(get_settings().database_dsn, statement_cache_size=0)
    transaction = connection.transaction()
    await transaction.start()
    try:
        ids = await _seed(connection)
        return await body(connection, ids)
    finally:
        await transaction.rollback()
        await connection.close()


def test_deletes_exactly_the_expired_and_aggregated_rows():
    async def body(connection, ids):
        report = await sweep_mentions(connection, RETENTION_DAYS, now=NOW)
        surviving = {
            row["id"]
            for row in await connection.fetch("SELECT id FROM mentions WHERE id = ANY($1)",
                                              list(ids.values()))
        }
        return report, surviving

    report, _surviving = run(_with_fixture(body))

    # three rows are past the cutoff
    assert report.expired == 3
    # two of them are safe to delete; the un-aggregated one is withheld
    assert report.deleted == 2
    assert report.withheld_unaggregated == 1


def test_withholds_matched_rows_that_were_never_rolled_up():
    async def body(connection, ids):
        await sweep_mentions(connection, RETENTION_DAYS, now=NOW)
        alive = await connection.fetchval(
            "SELECT count(*) FROM mentions WHERE id = $1", ids["old_unaggregated"]
        )
        gone = await connection.fetchval(
            "SELECT count(*) FROM mentions WHERE id = $1", ids["old_aggregated"]
        )
        unmatched_gone = await connection.fetchval(
            "SELECT count(*) FROM mentions WHERE id = $1", ids["old_unmatched"]
        )
        return alive, gone, unmatched_gone

    alive, gone, unmatched_gone = run(_with_fixture(body))
    assert alive == 1, "a matched mention with no mention_daily row must survive"
    assert gone == 0, "an aggregated, expired mention must be deleted"
    assert unmatched_gone == 0, "an unmatched expired mention has nothing waiting on it"


def test_rows_inside_the_window_are_untouched():
    async def body(connection, ids):
        await sweep_mentions(connection, RETENTION_DAYS, now=NOW)
        return await connection.fetchval(
            "SELECT count(*) FROM mentions WHERE id = ANY($1)",
            [ids["edge_inside"], ids["recent"]],
        )

    assert run(_with_fixture(body)) == 2


def test_aggregates_survive_the_sweep_intact():
    async def body(connection, ids):
        before = await connection.fetchrow(
            "SELECT mention_count, unique_authors, weighted_engagement "
            "FROM mention_daily WHERE issuer_id = $1", ids["kept_issuer"]
        )
        await sweep_mentions(connection, RETENTION_DAYS, now=NOW)
        after = await connection.fetchrow(
            "SELECT mention_count, unique_authors, weighted_engagement "
            "FROM mention_daily WHERE issuer_id = $1", ids["kept_issuer"]
        )
        total = await connection.fetchval("SELECT count(*) FROM mention_daily")
        return dict(before), dict(after), total

    before, after, total = run(_with_fixture(body))
    assert before == after, "the sweep must not touch aggregates"
    assert total == 1, "no aggregate row may be removed"


def test_sweep_is_idempotent():
    async def body(connection, ids):
        first = await sweep_mentions(connection, RETENTION_DAYS, now=NOW)
        second = await sweep_mentions(connection, RETENTION_DAYS, now=NOW)
        return first, second

    first, second = run(_with_fixture(body))
    assert first.deleted == 2
    assert second.deleted == 0, "a second sweep has nothing left to delete"
    assert second.expired == 1, "only the withheld row is still expired"


def test_cutoff_does_not_depend_on_time_of_day():
    async def body(connection, ids):
        morning = await sweep_mentions(
            connection, RETENTION_DAYS, now=NOW.replace(hour=3, minute=0)
        )
        return morning.cutoff

    cutoff_morning = run(_with_fixture(body))

    async def body_evening(connection, ids):
        evening = await sweep_mentions(
            connection, RETENTION_DAYS, now=NOW.replace(hour=22, minute=59)
        )
        return evening.cutoff

    cutoff_evening = run(_with_fixture(body_evening))
    assert cutoff_morning == cutoff_evening
    assert cutoff_morning.time() == datetime.min.time()
