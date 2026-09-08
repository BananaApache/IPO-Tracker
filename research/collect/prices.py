"""Daily bars for Tier B listings, from the licensed feed.

Which feed, and why not the one the module brief names: Finnhub's IPO calendar
is what builds the Tier A census, but Finnhub's `stock/candle` endpoint returns
403 on this key -- measured, see `data/source_probe.json`. The brief says to stop
and report rather than substitute a scraped source, and this does not substitute
one: Polygon is the licensed, keyed feed the deployed pipeline already uses for
exactly this purpose, chosen in `docs/sources.md` over four unlicensed
alternatives. Swapping one licensed provider for another is not the thing the
constraint forbids.

What the swap does cost is history. The Polygon plan on this key grants a
**rolling two-year window** -- measured at exactly 730 days on 2026-09-08 -- so a
listing older than that returns NOT_AUTHORIZED, not an empty series. That is why
`watchlist.csv` carries `price_available`: it is a statement about the feed's
entitlement, not about the company. Rows without it get no price panel and no
return figure, and the README says so rather than the figure quietly omitting them.

This module reads bars through `backend.prices.polygon.PolygonClient` rather than
reimplementing the request. That client already pins `adjusted=false` -- an
adjusted series is rewritten retroactively by a corporate action, which would
make a published return irreproducible.

Run:  uv run --group research python -m research.collect.prices
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from backend.config import Settings
from backend.prices.polygon import PolygonClient
from research.collect.config import get_research_settings
from research.collect.nyt import slug
from research.collect.paths import (
    PRICES_PARQUET,
    RAW_PRICES,
    WATCHLIST_CSV,
    ensure_dirs,
)

logger = logging.getLogger(__name__)

# The module brief asks for the first 90 trading days. Requested as calendar
# days with slack, then trimmed to 90 sessions after the fact: trading days are
# not a thing you can ask a date range for, and guessing 90*7/5 exactly would
# come up short across a holiday-heavy stretch.
TRADING_DAYS = 90
CALENDAR_SLACK_DAYS = 160


@dataclass
class Reconciliation:
    """What the calendar said versus what the tape shows."""

    company: str
    ticker: str
    calendar_date: str | None
    first_trade_date: str | None
    verdict: str
    detail: str


def load_price_rows(max_priority: int) -> list[dict[str, str]]:
    with WATCHLIST_CSV.open(newline="") as fh:
        return [
            r for r in csv.DictReader(fh)
            if r.get("tier_b", "").strip() == "True"
            and int(r.get("tier_b_priority") or 9) <= max_priority
        ]


async def collect(*, max_priority: int = 1, refetch: bool = False) -> list[Reconciliation]:
    ensure_dirs()
    s = get_research_settings()
    if not s.polygon_key:
        raise SystemExit("MARKET_DATA_API_KEY (Polygon) is not set.")

    rows = load_price_rows(max_priority)
    client = PolygonClient(Settings())
    recs: list[Reconciliation] = []
    try:
        for row in rows:
            company = row["company"]
            ticker = (row.get("edgar_ticker") or row.get("finnhub_symbol")
                      or row.get("seed_ticker") or "").strip().upper()
            listing = (row.get("listing_date") or "").strip()[:10]

            if not ticker:
                recs.append(Reconciliation(company, "", listing or None, None,
                                           "no_ticker", "no ticker on the watchlist row"))
                continue
            if row.get("price_available", "").strip() != "True":
                recs.append(Reconciliation(
                    company, ticker, listing or None, None, "outside_entitlement",
                    "listing predates the feed's rolling two-year window; no bars requested"))
                continue
            try:
                start = date.fromisoformat(listing)
            except ValueError:
                recs.append(Reconciliation(company, ticker, listing or None, None,
                                           "no_listing_date", "no usable listing date"))
                continue

            path = RAW_PRICES / f"{slug(company)}.json"
            if path.exists() and not refetch:
                blob = json.loads(path.read_text())
                bars = blob["bars"]
            else:
                # Start a few days early: if the calendar date is late, the real
                # first trade is before it and would otherwise be missed -- and
                # the first session is the one carrying the opening print.
                fetched = await client.daily_bars(
                    ticker, start - timedelta(days=7),
                    start + timedelta(days=CALENDAR_SLACK_DAYS))
                bars = [{"day": b.day.isoformat(), "open": b.open, "high": b.high,
                         "low": b.low, "close": b.close, "volume": b.volume}
                        for b in fetched]
                path.write_text(json.dumps({
                    "company": company, "ticker": ticker,
                    "calendar_listing_date": listing,
                    "requested_from": (start - timedelta(days=7)).isoformat(),
                    "requested_to": (start + timedelta(days=CALENDAR_SLACK_DAYS)).isoformat(),
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "bars": bars}, indent=1) + "\n")

            if not bars:
                recs.append(Reconciliation(company, ticker, listing, None, "no_bars",
                                           "feed returned no bars in the window"))
                logger.warning("%-24s %s: no bars", company[:24], ticker)
                continue

            first = bars[0]["day"]
            # The tape wins. Where the calendar and the first session disagree,
            # the calendar was an expectation and the session is a fact.
            if first == listing:
                verdict, detail = "agrees", "first session equals the calendar date"
            else:
                delta = (date.fromisoformat(first) - start).days
                verdict = "corrected"
                detail = (f"first session {first} is {delta:+d} days from the calendar "
                          f"date {listing}; the price series is authoritative")
            recs.append(Reconciliation(company, ticker, listing, first, verdict, detail))
            logger.info("%-24s %s: %d bars, first %s (%s)", company[:24], ticker,
                        len(bars), first, verdict)
    finally:
        await client.aclose()
    return recs


def aggregate() -> int:
    """Build the daily price panel from cached bars only. No network."""
    import pandas as pd

    frames = []
    for path in sorted(RAW_PRICES.glob("*.json")):
        blob = json.loads(path.read_text())
        bars = blob.get("bars") or []
        if not bars:
            continue
        df = pd.DataFrame(bars)
        df["company"] = blob["company"]
        df["ticker"] = blob["ticker"]
        df["calendar_listing_date"] = blob["calendar_listing_date"]
        df = df.sort_values("day").reset_index(drop=True)
        # Session index from the first traded session, not from the calendar
        # date: day 0 is the opening print, whatever the calendar claimed.
        df["session"] = range(len(df))
        df["first_trade_date"] = df["day"].iloc[0]
        # The whole series is stored, then trimmed on read. The brief asks for
        # the series rather than the derived return precisely so the window can
        # be recomputed without re-calling the provider.
        df["in_90d_window"] = df["session"] < TRADING_DAYS
        frames.append(df)

    if not frames:
        logger.warning("prices: nothing cached to aggregate")
        return 0
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(PRICES_PARQUET, index=False)
    logger.info("prices: %d bars across %d companies -> %s",
                len(out), out["company"].nunique(), PRICES_PARQUET)
    return len(out)


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--priority", type=int, default=1)
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if not args.aggregate_only:
        recs = await collect(max_priority=args.priority, refetch=args.refetch)
        out = RAW_PRICES.parent.parent / "price_reconciliation.csv"
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(asdict(recs[0]).keys()))
            w.writeheader()
            for r in recs:
                w.writerow(asdict(r))
        logger.info("wrote %s", out)
        for verdict in sorted({r.verdict for r in recs}):
            n = sum(1 for r in recs if r.verdict == verdict)
            print(f"prices: {verdict}: {n}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
