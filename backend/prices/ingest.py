"""Filling price_bars, and promoting listing events to 'listed'.

The listing date is the FIRST BAR, never the 8-A date. An 8-A precedes trading
by days, and a return measured from the wrong day is wrong invisibly.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import asyncpg

from backend.config import Settings
from backend.events.detect import NO_TRADE_AFTER_DAYS
from backend.prices.polygon import PolygonClient

logger = logging.getLogger(__name__)

# How far past the 8-A a first bar may appear and still belong to that event.
LISTING_WINDOW_DAYS = 15
# Days of price history to fetch after listing, per the study design.
STUDY_HORIZON_DAYS = 95


@dataclass
class PriceReport:
    considered: int = 0
    listed: int = 0
    no_price_data: int = 0
    registered_no_trade: int = 0
    bars_written: int = 0
    by_status: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"considered={self.considered} listed={self.listed} "
            f"no_data={self.no_price_data} no_trade={self.registered_no_trade} "
            f"bars={self.bars_written}"
        )


_TARGETS = """
    SELECT e.id, e.issuer_id, e.ticker, e.eight_a_filed_at::date AS eight_a_on, e.ipo_price
    FROM listing_events e
    WHERE e.ticker IS NOT NULL
      -- Operating companies only. SPACs, ETFs and re-listings are excluded
      -- from the study, so spending a 12-second rate-limit slot on each is
      -- wasted; the classification is stored and they stay queryable.
      AND e.cohort = 'operating'
      AND e.status <> 'listed'
    ORDER BY e.eight_a_filed_at DESC
"""

_INSERT_BAR = """
    INSERT INTO price_bars (issuer_id, day, open, high, low, close, volume, source)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
    ON CONFLICT (issuer_id, day) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
        close = EXCLUDED.close, volume = EXCLUDED.volume,
        source = EXCLUDED.source, fetched_at = now()
"""

_PROMOTE = """
    UPDATE listing_events SET
        status = $2, listed_on = $3, open_price = $4, updated_at = now()
    WHERE id = $1
"""


async def ingest_prices(
    pool: asyncpg.Pool,
    client: PolygonClient,
    settings: Settings,
    today: date | None = None,
    limit: int | None = None,
) -> PriceReport:
    report = PriceReport()
    today = today or datetime.now(UTC).date()

    async with pool.acquire() as connection:
        targets = await connection.fetch(_TARGETS)
    # Bounded so a scheduled run has a predictable ceiling. At 5 requests a
    # minute the provider's free tier is the clock, and an unbounded first run
    # would sit for hours.
    if limit is not None:
        targets = targets[:limit]
    report.considered = len(targets)

    for row in targets:
        eight_a = row["eight_a_on"]
        # Start a little before the 8-A: if bars exist there, this is not a
        # first listing and the event should not be promoted.
        start = eight_a - timedelta(days=10)
        end = min(today, eight_a + timedelta(days=STUDY_HORIZON_DAYS))

        try:
            bars = await client.daily_bars(row["ticker"], start, end)
        except Exception:
            logger.warning("prices: fetch failed for %s", row["ticker"], exc_info=True)
            continue

        if not bars:
            status = (
                "registered_no_trade"
                if (today - eight_a).days > NO_TRADE_AFTER_DAYS
                else "awaiting_ticker"
            )
            async with pool.acquire() as connection:
                await connection.execute(_PROMOTE, row["id"], status, None, None)
            report.no_price_data += status == "registered_no_trade"
            report.by_status[status] = report.by_status.get(status, 0) + 1
            continue

        first = bars[0]
        within = 0 <= (first.day - eight_a).days <= LISTING_WINDOW_DAYS

        async with pool.acquire() as connection, connection.transaction():
            for bar in bars:
                await connection.execute(
                    _INSERT_BAR, row["issuer_id"], bar.day, bar.open, bar.high,
                    bar.low, bar.close, bar.volume, client.source,
                )
                report.bars_written += 1

            if within:
                await connection.execute(
                    _PROMOTE, row["id"], "listed", first.day, first.open
                )
                report.listed += 1
                report.by_status["listed"] = report.by_status.get("listed", 0) + 1
            else:
                # Bars exist but not where a first listing would put them --
                # either trading predates the 8-A (a re-listing the periodic
                # report test missed) or the first bar is too late to attribute.
                await connection.execute(
                    _PROMOTE, row["id"], "no_price_data", None, None
                )
                report.no_price_data += 1
                report.by_status["no_price_data"] = report.by_status.get("no_price_data", 0) + 1

    return report


# Any issuer we have attention for, regardless of cohort. Separate from
# ingest_prices because that function also promotes listing_events to 'listed',
# and for an already-trading company the first bar is nowhere near its 8-A --
# it would be marked no_price_data. Here we only want the series.
_MENTIONED_WITH_TICKER = """
    SELECT i.id AS issuer_id, i.ticker,
           (SELECT min(posted_at)::date FROM mentions m
             WHERE m.issuer_id = i.id AND NOT m.needs_review) AS first_mention,
           (SELECT count(*) FROM price_bars b WHERE b.issuer_id = i.id) AS existing_bars
    FROM issuers i
    WHERE i.ticker IS NOT NULL
      AND EXISTS (SELECT 1 FROM mentions m
                   WHERE m.issuer_id = i.id AND NOT m.needs_review)
    ORDER BY (SELECT count(*) FROM mentions m
               WHERE m.issuer_id = i.id AND NOT m.needs_review) DESC
"""


async def ingest_bars_for_mentioned(
    pool: asyncpg.Pool,
    client: PolygonClient,
    settings: Settings,
    limit: int | None = None,
    pad_days: int = 10,
) -> PriceReport:
    """Daily bars for every issuer we have mentions for.

    Enables the daily-panel design: an issuer-day observation needs both a
    mention count and a return, and 78% of our mentions belonged to issuers
    with no price series at all.
    """
    report = PriceReport()
    today = datetime.now(UTC).date()

    async with pool.acquire() as connection:
        targets = await connection.fetch(_MENTIONED_WITH_TICKER)
    if limit is not None:
        targets = targets[:limit]
    report.considered = len(targets)

    for row in targets:
        if row["existing_bars"] > 0:
            continue
        start = (row["first_mention"] or today) - timedelta(days=pad_days)
        try:
            bars = await client.daily_bars(row["ticker"], start, today)
        except Exception:
            logger.warning("bars: fetch failed for %s", row["ticker"], exc_info=True)
            continue
        if not bars:
            report.no_price_data += 1
            continue

        async with pool.acquire() as connection, connection.transaction():
            for bar in bars:
                await connection.execute(
                    _INSERT_BAR, row["issuer_id"], bar.day, bar.open, bar.high,
                    bar.low, bar.close, bar.volume, client.source,
                )
                report.bars_written += 1
        report.listed += 1

    return report
