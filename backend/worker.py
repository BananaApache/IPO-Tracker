"""Scheduled ingestion. Runs as its own process, separate from the API.

Separate because the two have different failure modes and different resource
profiles: a poller that spends thirty seconds waiting on rate-limited HTTP must
not be sharing an event loop with request handling, and a crash in ingestion
should not take the API down with it. They share only the database.

    uv run python -m backend.worker           # schedule and stay up
    uv run python -m backend.worker --once    # one pass, then exit
"""

import argparse
import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import get_settings
from backend.db import create_pool
from backend.ingest.edgar import ingest_recent
from backend.events.detect import detect_events
from backend.ingest.offerings import extract_for_filings
from backend.ingest.retention import sweep_mentions
from backend.ingest.social import ingest_social
from backend.match.aliases import rebuild_for_all
from backend.sources.hackernews import HackerNewsAdapter
from backend.sec.client import SecClient, SecMisconfiguredError
from backend.sec.submissions import fetch_primary_documents

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("worker")


async def run_once(price_limit: int = 20) -> None:
    """One full cycle: EDGAR, listing events, prices, social, retention.

    This is what a scheduled runner invokes. Prices are bounded because the
    provider's free tier allows 5 requests a minute, so an unbounded pass would
    outlast any CI job.
    """
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        async with SecClient(settings) as client:
            report = await ingest_recent(pool, client, settings)
            logger.info("edgar ingest complete: %s", report)
            logger.info("listing events: %s", await detect_events(pool, client, settings))

        if settings.market_data_api_key:
            from backend.prices.ingest import ingest_prices
            from backend.prices.polygon import PolygonClient

            price_client = PolygonClient(settings)
            try:
                logger.info(
                    "price ingest: %s",
                    await ingest_prices(pool, price_client, settings, limit=price_limit),
                )
            finally:
                await price_client.aclose()
        await _social_job(pool, settings)
        async with pool.acquire() as connection:
            logger.info(
                "retention sweep: %s",
                await sweep_mentions(connection, settings.mention_retention_days),
            )
        if report.profiles_missing:
            logger.warning(
                "no submissions record for %d CIK(s): %s",
                len(report.profiles_missing),
                ", ".join(report.profiles_missing[:5]),
            )
    finally:
        await pool.close()


async def backfill_social(days: int, slice_days: int = 2) -> None:
    """Ingest social mentions across a long window, in slices.

    One fetch cannot span it: the Hacker News adapter is capped at 60,000 items
    per call and the source produces roughly 10,000 a day, so a single request
    for 210 days silently returns the most recent six. The window is walked
    backwards instead, moving both ends.
    """
    settings = get_settings()
    pool = await create_pool(settings)
    totals = {"fetched": 0, "candidates": 0, "accepted": 0, "review": 0, "inserted": 0}
    try:
        async with pool.acquire() as connection:
            written = await rebuild_for_all(connection)
        logger.info("aliases: %d new", written)

        end = datetime.now(UTC)
        floor = end - timedelta(days=days)
        while end > floor:
            start = max(floor, end - timedelta(days=slice_days))
            adapters = _build_adapters(settings)
            try:
                report = await ingest_social(pool, adapters, settings, since=start, until=end)
            finally:
                for adapter in adapters:
                    await adapter.aclose()
            totals["fetched"] += report.fetched
            totals["candidates"] += report.candidates
            totals["accepted"] += report.accepted
            totals["review"] += report.needs_review
            totals["inserted"] += report.inserted
            logger.info("social %s..%s: %s | running %s",
                        start.date(), end.date(), report, totals)
            end = start
        logger.info("social backfill complete over %d days: %s", days, totals)
    finally:
        await pool.close()


async def backfill_bars_for_mentioned() -> None:
    """Daily bars for every issuer we have mentions for, any cohort."""
    from backend.prices.ingest import ingest_bars_for_mentioned
    from backend.prices.polygon import PolygonClient

    settings = get_settings()
    pool = await create_pool(settings)
    try:
        client = PolygonClient(settings)
        try:
            report = await ingest_bars_for_mentioned(pool, client, settings)
        finally:
            await client.aclose()
        logger.info("bars for mentioned issuers: %s", report)
    finally:
        await pool.close()


async def backfill_prices() -> None:
    """Fetch daily bars for pending listing events and promote them to 'listed'."""
    from backend.prices.ingest import ingest_prices
    from backend.prices.polygon import PolygonClient

    settings = get_settings()
    pool = await create_pool(settings)
    try:
        client = PolygonClient(settings)
        try:
            report = await ingest_prices(pool, client, settings)
        finally:
            await client.aclose()
        logger.info("price ingest complete: %s", report)
        logger.info("statuses: %s", report.by_status)
    finally:
        await pool.close()


async def backfill_extraction(limit: int | None = None) -> None:
    """Extract offering terms from prospectus filings already ingested.

    Separate from the EDGAR backfill because it downloads 1-3 MB per filing.
    Scoped to issuers that have a 424B4 -- those are the ones whose deal terms
    the event study needs.
    """
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT f.id, f.issuer_id, f.cik, f.accession_no, f.form_type
                FROM filings f
                WHERE f.form_type IN ('424B4', 'S-1/A', 'F-1/A', 'S-1', 'F-1')
                  AND f.issuer_id IN (SELECT issuer_id FROM filings WHERE form_type = '424B4')
                  AND NOT EXISTS (
                      SELECT 1 FROM offerings o
                      WHERE o.issuer_id = f.issuer_id AND o.source_filing_id = f.id)
                ORDER BY f.form_type = '424B4' DESC, f.filed_at DESC
                """
            )
        filings = [dict(r) for r in rows][: limit or len(rows)]
        logger.info("extraction backfill: %d filings queued", len(filings))

        async with SecClient(settings) as client:
            documents: dict[str, dict[str, str]] = {}
            for cik in sorted({f["cik"] for f in filings}):
                documents[cik] = await fetch_primary_documents(client, cik)
            report = await extract_for_filings(pool, client, settings, filings, documents)
        logger.info("extraction backfill complete: %s", report)
        logger.info("methods: %s", report.methods)
    finally:
        await pool.close()


async def detect_listing_events() -> None:
    """Populate listing_events from 8-A filings already in the database."""
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        async with SecClient(settings) as client:
            report = await detect_events(pool, client, settings)
        logger.info("listing events: %s", report)
    finally:
        await pool.close()


async def backfill_edgar(days: int) -> None:
    """One-off historical ingest. Issuers and filings only."""
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        async with SecClient(settings) as client:
            report = await ingest_recent(
                pool, client, settings, lookback_days=days, extract_offerings=False
            )
        logger.info("edgar backfill (%d days): %s", days, report)
        async with pool.acquire() as connection:
            written = await rebuild_for_all(connection)
        logger.info("aliases rebuilt: %d new", written)
    finally:
        await pool.close()


def _build_adapters(settings):
    # GDELT was cut -- see docs/sources.md.
    adapters = [HackerNewsAdapter(settings)]

    # Reddit is registered only when OAuth credentials exist, so an
    # unconfigured deployment makes no request to reddit.com at all.
    if settings.reddit_client_id and settings.reddit_client_secret:
        from backend.sources.reddit import RedditAdapter

        adapters.append(RedditAdapter(settings))
        logger.info("reddit adapter enabled (r/%s)", settings.reddit_subreddits)

    return adapters


async def _social_job(pool, settings) -> None:
    """Fetch social mentions and resolve them to issuers. Never raises."""
    try:
        # Aliases are regenerated first: an issuer ingested from EDGAR an hour
        # ago has none yet, and a mention of it would be unmatchable.
        async with pool.acquire() as connection:
            written = await rebuild_for_all(connection)
        if written:
            logger.info("aliases: %d new", written)

        adapters = _build_adapters(settings)
        try:
            report = await ingest_social(pool, adapters, settings)
        finally:
            for adapter in adapters:
                await adapter.aclose()
        logger.info("social ingest complete: %s", report)
    except Exception:
        logger.exception("social ingest failed; will retry on the next tick")


async def _keep_warm_job(pool, settings) -> None:
    """Touch the database so a serverless Postgres does not autosuspend.

    Off unless KEEP_WARM_MINUTES is set. The trade is compute-hours against
    cold-start latency, and on Neon's free tier a permanent ping loses that
    trade badly -- see docs/deploy.md.
    """
    try:
        async with pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
    except Exception:
        logger.warning("keep-warm ping failed", exc_info=True)


async def _retention_job(pool, settings) -> None:
    """Delete raw mentions past the retention window. Never raises."""
    try:
        async with pool.acquire() as connection:
            report = await sweep_mentions(connection, settings.mention_retention_days)
        logger.info("retention sweep: %s", report)
    except Exception:
        logger.exception("retention sweep failed; will retry on the next tick")


async def _job(pool, settings) -> None:
    """One scheduled pass. Never raises -- an exception escaping here would
    stop APScheduler from rescheduling the job."""
    try:
        async with SecClient(settings) as client:
            report = await ingest_recent(pool, client, settings)
        logger.info("edgar ingest complete: %s", report)
    except SecMisconfiguredError:
        # Retrying cannot fix a rejected User-Agent, so say so loudly rather
        # than burying it in a retry loop that looks like a network problem.
        logger.exception("SEC rejected our identity; fix SEC_USER_AGENT")
    except Exception:
        logger.exception("edgar ingest failed; will retry on the next tick")


async def serve() -> None:
    settings = get_settings()
    pool = await create_pool(settings)
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _job,
        trigger=IntervalTrigger(minutes=settings.sec_poll_interval_minutes),
        args=[pool, settings],
        id="edgar_ingest",
        # A slow run must not stack on the next tick: EDGAR ingestion is
        # rate-limited, so two concurrent passes would halve the effective
        # limit per pass and race on the same upserts.
        max_instances=1,
        # After downtime, run once rather than once per missed tick.
        coalesce=True,
        misfire_grace_time=None,
        next_run_time=None,
    )

    scheduler.add_job(
        _social_job,
        trigger=IntervalTrigger(minutes=settings.social_poll_interval_minutes),
        args=[pool, settings],
        id="social_ingest",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _retention_job,
        # Daily, not hourly: the cutoff is a whole UTC day, so running it more
        # often deletes nothing new. Offset from midnight so it does not race
        # the nightly rollup that has to run first -- a mention deleted before
        # its day is aggregated is gone for good, which is why sweep_mentions
        # withholds rows that have no mention_daily row yet.
        trigger=CronTrigger(hour=4, minute=30, timezone="UTC"),
        args=[pool, settings],
        id="mention_retention",
        max_instances=1,
        coalesce=True,
    )

    if settings.keep_warm_minutes:
        scheduler.add_job(
            _keep_warm_job,
            trigger=IntervalTrigger(minutes=settings.keep_warm_minutes),
            args=[pool, settings],
            id="keep_warm",
            max_instances=1,
            coalesce=True,
        )
        logger.info("keep-warm enabled: every %d min", settings.keep_warm_minutes)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    scheduler.start()
    logger.info(
        "worker up: edgar ingest every %d min (%d-day lookback, %.1f req/s to SEC); "
        "retention sweep daily at 04:30 UTC (%d-day window)",
        settings.sec_poll_interval_minutes,
        settings.sec_lookback_days,
        settings.sec_rate_limit_per_second,
        settings.mention_retention_days,
    )

    # Run immediately on boot so a fresh deploy is not blind until the first
    # interval elapses.
    await _job(pool, settings)

    try:
        await stop.wait()
    finally:
        logger.info("worker shutting down")
        scheduler.shutdown(wait=False)
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="IPO surveillance ingestion worker")
    parser.add_argument("--once", action="store_true", help="run one full cycle and exit")
    parser.add_argument(
        "--price-limit", type=int, default=20,
        help="max listing events to fetch prices for in a --once run",
    )
    parser.add_argument(
        "--backfill-bars-mentioned", action="store_true",
        help="daily bars for every issuer with mentions, any cohort",
    )
    parser.add_argument(
        "--backfill-social", type=int, metavar="DAYS",
        help="ingest social mentions across a long window, in slices",
    )
    parser.add_argument(
        "--backfill-prices", action="store_true",
        help="fetch daily bars for pending listing events",
    )
    parser.add_argument(
        "--backfill-extraction", type=int, nargs="?", const=0, metavar="LIMIT",
        help="extract offering terms from prospectus filings already ingested",
    )
    parser.add_argument(
        "--detect-events", action="store_true",
        help="populate listing_events from 8-A filings already ingested",
    )
    parser.add_argument(
        "--backfill-edgar", type=int, metavar="DAYS",
        help="one-off historical EDGAR ingest, skipping prospectus extraction",
    )
    args = parser.parse_args()

    if args.backfill_bars_mentioned:
        asyncio.run(backfill_bars_for_mentioned())
    elif args.backfill_social:
        asyncio.run(backfill_social(args.backfill_social))
    elif args.backfill_prices:
        asyncio.run(backfill_prices())
    elif args.backfill_extraction is not None:
        asyncio.run(backfill_extraction(args.backfill_extraction or None))
    elif args.detect_events:
        asyncio.run(detect_listing_events())
    elif args.backfill_edgar:
        asyncio.run(backfill_edgar(args.backfill_edgar))
    elif args.once:
        asyncio.run(run_once(args.price_limit))
    else:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(serve())


if __name__ == "__main__":
    main()
