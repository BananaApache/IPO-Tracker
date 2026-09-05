"""The 90-day retention sweep.

Deletes raw `mentions` rows once they age past the window. `mention_daily`
aggregates derived from them are permanent -- that asymmetry is what keeps this
project a metrics pipeline rather than an archive of other people's posts.

Takes a connection rather than a pool so a test can run it inside a transaction
and roll back. A function that deletes rows should be exercisable without a
disposable database.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class RetentionReport:
    cutoff: datetime
    expired: int = 0
    deleted: int = 0
    withheld_unaggregated: int = 0

    def __str__(self) -> str:
        return (
            f"cutoff={self.cutoff:%Y-%m-%d} expired={self.expired} "
            f"deleted={self.deleted} withheld={self.withheld_unaggregated}"
        )


# A matched mention is only safe to delete once its day has been rolled up:
# once the row is gone the aggregate can never be rebuilt. Unmatched mentions
# (issuer_id IS NULL) never enter mention_daily -- there is no issuer to
# aggregate them under -- so nothing is waiting on them and they always expire.
_AGGREGATED = """
    m.issuer_id IS NULL
    OR EXISTS (
        SELECT 1 FROM mention_daily d
        WHERE d.issuer_id = m.issuer_id
          AND d.source    = m.source
          AND d.day       = (m.posted_at AT TIME ZONE 'UTC')::date
    )
"""

_COUNT_EXPIRED = "SELECT count(*) FROM mentions m WHERE m.posted_at < $1"

_COUNT_WITHHELD = f"""
    SELECT count(*) FROM mentions m
    WHERE m.posted_at < $1 AND NOT ({_AGGREGATED})
"""

_DELETE = f"""
    DELETE FROM mentions m
    WHERE m.posted_at < $1 AND ({_AGGREGATED})
"""


async def sweep_mentions(
    connection: asyncpg.Connection,
    retention_days: int,
    now: datetime | None = None,
) -> RetentionReport:
    now = now or datetime.now(UTC)
    # Whole-day cutoff in UTC, so a sweep run at 03:00 and one run at 22:00 on
    # the same day delete exactly the same rows.
    cutoff = datetime.combine(
        (now - timedelta(days=retention_days)).date(), datetime.min.time(), tzinfo=UTC
    )

    report = RetentionReport(cutoff=cutoff)
    report.expired = await connection.fetchval(_COUNT_EXPIRED, cutoff)
    if not report.expired:
        return report

    report.withheld_unaggregated = await connection.fetchval(_COUNT_WITHHELD, cutoff)
    status = await connection.execute(_DELETE, cutoff)
    report.deleted = int(status.rsplit(" ", 1)[-1])

    if report.withheld_unaggregated:
        # Not an error, but it means the rollup is behind. Silently keeping the
        # rows would look like the sweep is broken; silently deleting them would
        # destroy signal that was never aggregated.
        logger.warning(
            "retention: withheld %d expired mention(s) with no mention_daily row; "
            "the rollup is behind",
            report.withheld_unaggregated,
        )
    return report
