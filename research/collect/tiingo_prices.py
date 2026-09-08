"""Daily prices from Tiingo: the licensed feed with enough history to matter.

Why a fourth price provider. The underpricing study is capped at n=12 purely by
history depth, and four free tiers were tried before this one:

  * **Polygon** -- 730-day entitlement, and it is *account-wide*:
    `/v1/open-close`, `/v2/aggs/grouped` and `/v3/trades` all refuse 2019 dates.
  * **Finnhub** -- `/stock/candle` returns 403 on this key.
  * **FMP** -- correct endpoint and 5 years of history, but a symbol allowlist
    admits only 8 of 39 watchlist tickers; even `/quote` refuses `RDDT`.
  * **Alpha Vantage** -- no symbol restriction, but `outputsize=full` and
    intraday's historical `month` are premium, `compact` gives 100 days, and the
    free weekly series *omits the IPO's first trading week* -- verified on six
    companies. Beware its `demo` key, which serves `outputsize=full` and so makes
    the free tier look more capable than it is.

Tiingo is documented, keyed, and returns as-traded daily bars for every ticker
tried, back to 2019 and beyond. That satisfies the same bar as Polygon and
Finnhub -- a provider granting the use with an API key -- unlike the sources
`docs/sources.md` rejected (Yahoo, Stooq, SerpAPI, unauthenticated Reddit) and
unlike Dukascopy's keyless binary feed, whose own client library states it is
"not affiliated, endorsed, or vetted by Dukascopy Bank SA".

**Raw, not adjusted.** Tiingo returns both; this stores `open`/`close`
(as-traded) and keeps `adjOpen`/`adjClose` alongside for inspection. An adjusted
series is rewritten retroactively by a split, which would make a published
underpricing figure irreproducible -- the same reason
`backend/prices/polygon.py` pins `adjusted=false`.

One call per ticker covers the whole window, so the entire watchlist costs 39
calls. Every response is cached, so a re-run costs nothing.

Run:  uv run --group research python -m research.collect.tiingo_prices
      uv run --group research python -m research.collect.tiingo_prices --validate
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta

from backend.http import RetryingClient
from research.collect.nyt import slug
from research.collect.paths import DATA, RAW, ensure_dirs

logger = logging.getLogger(__name__)

BASE = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
RAW_TIINGO = RAW / "tiingo"
TIINGO_PARQUET = DATA / "tiingo_daily.parquet"

# Enough calendar days past the listing to contain 90 trading sessions, with
# slack for holidays -- the same reasoning as collect/prices.py.
CALENDAR_SLACK = 160


def _key() -> str:
    for line in open(".env"):
        if line.startswith("TIINGOAPI_KEY"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIINGOAPI_KEY is not set in .env")


async def collect(*, refetch: bool = False) -> dict[str, int]:
    import pandas as pd

    ensure_dirs()
    RAW_TIINGO.mkdir(parents=True, exist_ok=True)
    token = _key()

    wl = pd.read_csv(DATA / "watchlist.csv")
    rows = [r for r in wl[wl["tier_b"]].to_dict("records")
            if str(r.get("finnhub_symbol") or "") not in ("", "nan")
            and str(r.get("listing_date") or "")[:4].isdigit()]
    logger.info("tiingo: %d Tier B companies with a ticker and listing date",
                len(rows))

    client = RetryingClient(user_agent="IPOTracker-research/0.1", per_second=1.0,
                            max_retries=4, base_backoff=5.0, max_backoff=60.0)
    stats = {"fetched": 0, "cached": 0, "empty": 0, "failed": 0}
    try:
        for row in rows:
            ticker = str(row["finnhub_symbol"]).upper()
            path = RAW_TIINGO / f"{slug(row['company'])}.json"
            if path.exists() and not refetch:
                stats["cached"] += 1
                continue
            listing = date.fromisoformat(str(row["listing_date"])[:10])
            # Start a few days early: if the calendar date is late, the real
            # first trade precedes it and would otherwise be missed.
            start = listing - timedelta(days=7)
            end = listing + timedelta(days=CALENDAR_SLACK)
            try:
                bars = await client.get_json(
                    BASE.format(ticker=ticker),
                    params={"startDate": start.isoformat(),
                            "endDate": end.isoformat(), "token": token},
                )
            except Exception as exc:
                logger.warning("%-24s %s: %s", row["company"][:24], ticker,
                               type(exc).__name__)
                stats["failed"] += 1
                continue
            if not isinstance(bars, list) or not bars:
                logger.warning("%-24s %s: no bars returned", row["company"][:24],
                               ticker)
                stats["empty"] += 1
                continue
            path.write_text(json.dumps({
                "company": row["company"],
                "ticker": ticker,
                "calendar_listing_date": listing.isoformat(),
                "requested": [start.isoformat(), end.isoformat()],
                "fetched_at": datetime.now(UTC).isoformat(),
                "provider": "tiingo (licensed, keyed)",
                "bars": bars,
            }, indent=1) + "\n")
            stats["fetched"] += 1
            logger.info("%-24s %s: %d bars, first %s", row["company"][:24],
                        ticker, len(bars), bars[0]["date"][:10])
    finally:
        await client.aclose()
    return stats


def aggregate() -> int:
    """Build the daily panel from cached bars. No network."""
    import pandas as pd

    frames = []
    for path in sorted(RAW_TIINGO.glob("*.json")):
        blob = json.loads(path.read_text())
        bars = blob.get("bars") or []
        if not bars:
            continue
        df = pd.DataFrame(bars)
        df["day"] = pd.to_datetime(df["date"]).dt.date.astype(str)
        df = df.sort_values("day").reset_index(drop=True)
        df["company"] = blob["company"]
        df["ticker"] = blob["ticker"]
        df["calendar_listing_date"] = blob["calendar_listing_date"]
        # Session index from the first traded session, not the calendar date:
        # day 0 is the opening print whatever the calendar claimed.
        df["session"] = range(len(df))
        df["first_trade_date"] = df["day"].iloc[0]
        df["in_90d_window"] = df["session"] < 90
        frames.append(df[["company", "ticker", "day", "session", "open", "high",
                          "low", "close", "volume", "adjOpen", "adjClose",
                          "calendar_listing_date", "first_trade_date",
                          "in_90d_window"]])
    if not frames:
        logger.warning("tiingo: nothing cached to aggregate")
        return 0
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(TIINGO_PARQUET, index=False)
    logger.info("tiingo: %d bars across %d companies -> %s", len(out),
                out["company"].nunique(), TIINGO_PARQUET)
    return len(out)


def validate() -> None:
    """Cross-check Tiingo against Polygon where both cover the same session.

    Two licensed feeds disagreeing on an as-traded price means one of them is
    adjusting, and a published underpricing figure would depend on which was
    used. Worth knowing before trusting the 27 companies only Tiingo covers.
    """
    import pandas as pd

    if not TIINGO_PARQUET.exists():
        raise SystemExit("no tiingo parquet; run the collector first.")
    ti = pd.read_parquet(TIINGO_PARQUET)
    po = pd.read_parquet(DATA / "prices_daily.parquet")
    merged = po.merge(ti, on=["company", "day"], suffixes=("_poly", "_tiingo"))
    if merged.empty:
        print("no overlapping sessions to compare")
        return
    merged["d_open"] = (merged["open_poly"] - merged["open_tiingo"]).abs()
    merged["d_close"] = (merged["close_poly"] - merged["close_tiingo"]).abs()
    tol = 0.02
    bad = merged[(merged["d_open"] > tol) | (merged["d_close"] > tol)]
    print(f"overlapping sessions compared : {len(merged)}")
    print(f"companies in common           : {merged['company'].nunique()}")
    print(f"disagreements over ${tol:.2f}      : {len(bad)}")
    print(f"max |open| diff               : ${merged['d_open'].max():.4f}")
    print(f"max |close| diff              : ${merged['d_close'].max():.4f}")
    if len(bad):
        print("\nworst rows:")
        print(bad.nlargest(5, "d_close")[
            ["company", "day", "open_poly", "open_tiingo", "close_poly",
             "close_tiingo"]].to_string(index=False))


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--aggregate-only", action="store_true")
    ap.add_argument("--validate", action="store_true",
                    help="cross-check against Polygon on overlapping sessions")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if args.validate:
        validate()
        return 0
    if not args.aggregate_only:
        stats = await collect(refetch=args.refetch)
        print(f"tiingo: fetched={stats['fetched']} cached={stats['cached']} "
              f"empty={stats['empty']} failed={stats['failed']}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
