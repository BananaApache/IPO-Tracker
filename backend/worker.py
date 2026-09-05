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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import get_settings
from backend.db import create_pool
from backend.ingest.edgar import ingest_recent
from backend.ingest.retention import sweep_mentions
from backend.sec.client import SecClient, SecMisconfiguredError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("worker")


async def run_once() -> None:
    settings = get_settings()
    pool = await create_pool(settings)
    try:
        async with SecClient(settings) as client:
            report = await ingest_recent(pool, client, settings)
        logger.info("edgar ingest complete: %s", report)
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
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_once())
    else:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(serve())


if __name__ == "__main__":
    main()
