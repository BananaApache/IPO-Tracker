"""X/Twitter monthly post counts, via twitterapis.com.

A third-party scraper service, not the official X API. PROJECT_BRIEF.md section 7
rules those out for the deployed pipeline; the research-module brief approves
them for `research/` only. So everything here stays inside this module, its
output lives in its own tables, and nothing it produces reaches the deployed
pipeline or gets blended into a news count. The README repeats this and so does
every figure that uses the data.

Two measured facts drive the design (see `data/source_probe.json`):

  * **It reaches 2019.** Unlike GNews (30 days of history on the free plan) and
    unlike redditapis (no date-range parameter at all), this service answers
    `since:`/`until:` windows across the whole study period. That makes it the
    only social source in the brief that can see a pre-S-1 window.
  * **There is no total-count field.** The response carries a page of ~20 tweets
    and a cursor, so volume has to be paginated out, and each page is a billed
    call at roughly $0.0008.

**Why this does not measure a monthly count.** Results come back newest-first
from the `until:` boundary, so a page budget is spent at the top of the window
and works downward. Measured on the calibration sample: `"Figma"` with a
ten-page budget returned 200 tweets for July 2023 of which **zero** fell in July
-- the budget was exhausted within hours of the 1 August boundary, because Figma
is discussed constantly. A "count" of 0 for the most-discussed design tool on the
platform is not a small number, it is a broken measurement, and no `censored`
flag makes it safe to plot.

So the metric is **density, not count**: tweets per day over the span the page
budget actually reached. For a quiet month the budget exhausts the whole month
and density is the true monthly rate. For a busy month it covers a few hours and
density is estimated from those hours. Either way the number means the same
thing and is comparable across companies, which a censored count is not.

What that buys, and what it costs: cost per company-month becomes fixed and
predictable rather than proportional to the company's fame. The bias is that a
busy month is sampled from its **final hours** rather than uniformly, so a spike
earlier in the month is under-weighted. `anchor_days` samples several points
inside the month and averages, which reduces but does not remove that. Every
record carries `reached_month_start` so the analysis can separate months
measured completely from months estimated from a tail.

Every tweet's timestamp is stored, so span, density and count are all
recomputable offline without re-billing.

`until:` is not trusted as a filter. The probe returned tweets stamped
2019-02-01 for a query bounded `until:2019-01-31`, so every tweet's
`created_at` is parsed and re-checked against the month client-side, and the
count is of tweets that actually fall in the window.

Run:  uv run --group research python -m research.collect.twitter --calibrate
      uv run --group research python -m research.collect.twitter --budget 2000
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from research.collect.config import get_research_settings
from research.collect.nyt import load_tier_b, month_starts, slug, window_for
from research.collect.paths import (
    RAW_TWITTER,
    TWITTER_COUNTS_PARQUET,
    ensure_dirs,
)

logger = logging.getLogger(__name__)


class InsufficientCredits(RuntimeError):
    """The service's prepaid balance is exhausted. Retrying cannot fix it."""

SEARCH_URL = "https://api.twitterapis.com/twitter/tweet/advanced_search"
COST_PER_CALL_USD = 0.0008
# 20 tweets per page, so 10 pages sees ~200 tweets per company-month. A fixed
# budget rather than one scaled to the company: the density metric is defined at
# any volume, so a famous name costs the same as a quiet one and the spend is
# predictable in advance.
DEFAULT_MAX_PAGES = 10

# A month whose observed coverage falls below this is NOT reported as a density.
# Extrapolating a monthly rate from a sliver of the boundary is arithmetic, not
# measurement: a calibration month for "Circle" covered 0.0002 of January 2024
# and the ratio came out at 10,971 tweets/day.
#
# Chosen after seeing the calibration coverage distribution, and that is
# deliberate and safe: it is a threshold on the INSTRUMENT's reach, decided
# without reference to any attention-versus-return result. It does not touch the
# outcome variable, and the months it excludes are excluded for being
# unmeasurable, not for being inconvenient.
MIN_COVERAGE = 0.20


def parse_created_at(raw: str | None) -> datetime | None:
    """X stamps look like 'Fri Feb 01 04:05:59 +0000 2019'."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return None


def month_bounds(m: date) -> tuple[date, date]:
    nxt = (m.replace(year=m.year + 1, month=1) if m.month == 12
           else m.replace(month=m.month + 1))
    return m, nxt


async def measure_month(client: httpx.AsyncClient, key: str, query: str, m: date,
                        *, max_pages: int, anchors: int = 1) -> dict:
    """Measure one company-month. Returns the record written to disk.

    Pages newest-first from each anchor and stops at the page cap or when the
    month's start is reached. `anchors` > 1 splits the budget across several
    points inside the month, which trades depth at the boundary for coverage
    across the month.
    """
    start, end = month_bounds(m)
    days_in_month = (end - start).days
    pages_per_anchor = max(1, max_pages // anchors)

    # Anchors overlap by construction when volume is low, so their covered
    # intervals are UNIONED, not summed. Summing them counts the same stretch of
    # calendar twice and understates the rate; the earlier version of this
    # produced a 63-day span for a 31-day month.
    covered: list[tuple[datetime, datetime]] = []
    all_stamps: list[datetime] = []
    seen_ids: set[str] = set()
    pages = 0
    exhausted_any = False
    errors: list[str] = []

    # Anchor upper bounds, latest first. `until:` was measured to be loose by
    # about a day, so the bound is set a day past the intended edge and the real
    # filtering is done here on parsed timestamps.
    anchor_bounds = [end - timedelta(days=int(i * days_in_month / anchors))
                     for i in range(anchors)]
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(end, datetime.min.time(), tzinfo=UTC)

    for bound in anchor_bounds:
        cursor: str | None = None
        anchor_pages = 0
        anchor_stamps: list[datetime] = []
        exhausted = False
        while anchor_pages < pages_per_anchor:
            params = {"query": f"{query} since:{start.isoformat()} "
                               f"until:{(bound + timedelta(days=1)).isoformat()}",
                      "queryType": "Latest"}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(SEARCH_URL, params=params,
                                    headers={"Authorization": f"Bearer {key}"},
                                    timeout=45.0)
            pages += 1
            anchor_pages += 1
            if resp.status_code == 402:
                # Prepaid balance exhausted. Abort the whole run rather than
                # walking the remaining months and writing a useless record for
                # each -- see the caching note in `collect`.
                raise InsufficientCredits(resp.text[:160])
            if resp.status_code != 200:
                errors.append(f"HTTP {resp.status_code}: {resp.text[:120]}")
                break
            body = resp.json()
            tweets = body.get("tweets") or []
            if not tweets:
                exhausted = True
                break
            for t in tweets:
                tid = t.get("id")
                # The cursor can re-serve a tweet across a page boundary, and
                # anchors deliberately overlap. Counting ids keeps either from
                # inflating the density.
                if tid and tid in seen_ids:
                    continue
                if tid:
                    seen_ids.add(tid)
                ts = parse_created_at(t.get("created_at"))
                if ts:
                    all_stamps.append(ts)
                    if start <= ts.date() < end:
                        anchor_stamps.append(ts)
            cursor = body.get("next_cursor") or None
            if not cursor:
                # The provider ran out of results inside the window. That IS
                # reaching the start: the absence of older tweets is data, not a
                # budget failure. The previous version additionally required the
                # oldest tweet to land on the 1st, so a quiet month whose first
                # tweet fell on the 5th was wrongly marked incomplete.
                exhausted = True
                break

        # The covered interval is what the provider actually delivered, not
        # what was asked for. `until:` is honoured loosely -- the probe saw
        # 2019-02-01 stamps for an until:2019-01-31 query -- so a requested
        # bound can sit BEFORE the returned tweets and produce an inverted
        # interval. An earlier version used the requested bound here and
        # reported a negative span.
        if exhausted:
            exhausted_any = True
            # Exhausted: everything from the month start to this bound was
            # searched, whether or not it held tweets.
            hi = min(datetime.combine(bound, datetime.max.time(), tzinfo=UTC), end_dt)
            covered.append((start_dt, hi))
        elif anchor_stamps:
            lo, hi = min(anchor_stamps), max(anchor_stamps)
            lo = max(lo, start_dt)
            hi = min(hi, end_dt)
            if hi > lo:
                covered.append((lo, hi))

    in_window = [t for t in all_stamps if start <= t.date() < end]

    # Union of covered intervals.
    union_days = 0.0
    if covered:
        merged: list[list[datetime]] = []
        for lo, hi in sorted(covered):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        union_days = sum((hi - lo).total_seconds() for lo, hi in merged) / 86400.0
    union_days = min(max(union_days, 0.0), float(days_in_month))

    reached_start = exhausted_any or union_days >= days_in_month - 1e-9
    effective_span = float(days_in_month) if reached_start else union_days
    coverage = (effective_span / days_in_month) if days_in_month else 0.0

    # Below the coverage floor the month is busier than the budget can measure.
    # Reported as right-censored rather than as a number: "at least this busy",
    # with no rate claimed.
    exceeds_budget = (not reached_start) and coverage < MIN_COVERAGE
    density = (len(in_window) / effective_span
               if effective_span > 0 and not exceeds_budget else None)

    return {
        # tweets/day. The comparable quantity. None means nothing in-window was
        # reached, which is a coverage failure, not a zero.
        "density_per_day": round(density, 4) if density is not None else None,
        # True: this month is busier than the page budget can measure, so
        # in_window_tweets is a floor and no rate is claimed.
        "volume_exceeds_budget": exceeds_budget,
        "in_window_tweets": len(in_window),
        "out_of_window_tweets": len(all_stamps) - len(in_window),
        "observed_span_days": round(effective_span, 4),
        "covered_intervals": [[lo.isoformat(), hi.isoformat()] for lo, hi in covered],
        "union_span_days": round(union_days, 4),
        "days_in_month": days_in_month,
        # True: the budget reached the month's start, so in_window_tweets is a
        # complete monthly count. False: it is a tail sample and only the
        # density is meaningful.
        "reached_month_start": reached_start,
        "coverage_fraction": round(coverage, 4),
        "min_coverage_required": MIN_COVERAGE,
        "pages": pages,
        "max_pages": max_pages,
        "anchors": anchors,
        # Stored so span, density and count are all recomputable offline.
        "timestamps": sorted(t.isoformat() for t in all_stamps),
        "errors": errors or None,
    }


async def collect(*, call_budget: int, max_priority: int, max_pages: int,
                  months_limit: int = 0, anchors: int = 1) -> dict[str, int | float]:
    ensure_dirs()
    s = get_research_settings()
    if not s.twitter_key:
        raise SystemExit("TWITTERAPI_KEY is not set.")

    rows = load_tier_b(max_priority)
    jobs: list[tuple[dict, date]] = []
    for row in rows:
        win = window_for(row)
        if win is None:
            continue
        months = month_starts(*win)
        if months_limit:
            # Calibration takes months spread across the window rather than the
            # first N: the first N are all pre-filing and all quiet, which would
            # under-estimate the page cost of the months that matter.
            step = max(1, len(months) // months_limit)
            months = months[::step][:months_limit]
        for m in months:
            path = RAW_TWITTER / slug(row["company"]) / f"{m:%Y-%m}.json"
            if not path.exists():
                jobs.append((row, m))

    logger.info("twitter: %d company-months to fetch, budget %d calls "
                "(~$%.2f at $%.4f/call)", len(jobs), call_budget,
                call_budget * COST_PER_CALL_USD, COST_PER_CALL_USD)

    stats = {"fetched": 0, "calls": 0, "incomplete": 0, "unmeasurable": 0,
             "errored": 0, "remaining": 0}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for row, m in jobs:
            if stats["calls"] + max_pages > call_budget:
                stats["remaining"] = len(jobs) - stats["fetched"]
                logger.info("twitter: budget would be exceeded; stopping with %d left",
                            stats["remaining"])
                break
            try:
                rec = await measure_month(client, s.twitter_key, row["twitter_query"],
                                          m, max_pages=max_pages, anchors=anchors)
            except InsufficientCredits as exc:
                logger.error("twitter: out of credits (%s). Stopping. %d months "
                             "were not collected and NOTHING was cached for them.",
                             str(exc)[:120], len(jobs) - stats["fetched"])
                stats["remaining"] = len(jobs) - stats["fetched"]
                break

            # A month whose requests errored is NOT cached. The cache is also
            # the resume mechanism -- a month on disk is never re-requested --
            # so writing a failed month as "0 tweets, coverage 0" would make a
            # later run skip it and treat an API failure as a measured zero.
            # That is the difference between missing data and a false zero.
            if rec.get("errors"):
                logger.warning("twitter: %-22s %s -> errors, not cached: %s",
                               row["company"][:22], f"{m:%Y-%m}",
                               str(rec["errors"][0])[:90])
                stats["errored"] += 1
                continue

            path = RAW_TWITTER / slug(row["company"]) / f"{m:%Y-%m}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "company": row["company"],
                "query": row["twitter_query"],
                "month": f"{m:%Y-%m}",
                "fetched_at": datetime.now(UTC).isoformat(),
                "service": "twitterapis.com (third-party scraper, not the official X API)",
                **rec,
            }, indent=1) + "\n")
            stats["fetched"] += 1
            stats["calls"] += rec.get("pages") or 0
            stats["incomplete"] += 0 if rec.get("reached_month_start") else 1
            stats["unmeasurable"] += 1 if rec.get("volume_exceeds_budget") else 0
            if rec.get("volume_exceeds_budget"):
                state = f" EXCEEDS BUDGET (>= {rec.get('in_window_tweets')} tweets)"
            elif rec.get("reached_month_start"):
                state = " complete"
            else:
                state = " tail-sample"
            logger.info("twitter: %-22s %s -> %s/day (%s in-window, %s pages, "
                        "coverage %s)%s", row["company"][:22], f"{m:%Y-%m}",
                        rec.get("density_per_day"), rec.get("in_window_tweets"),
                        rec.get("pages"), rec.get("coverage_fraction"), state)
    stats["est_cost_usd"] = round(stats["calls"] * COST_PER_CALL_USD, 4)
    return stats


def recompute() -> int:
    """Rebuild every month's metrics from the stored timestamps. No network.

    Every tweet timestamp and every covered interval is written to the raw
    record precisely so the estimator can be revised without re-billing the
    service. Use this after changing the density definition -- re-collecting to
    fix an arithmetic bug spends money to obtain bytes already on disk.
    """
    n = 0
    for company_dir in sorted(RAW_TWITTER.iterdir()):
        if not company_dir.is_dir():
            continue
        for path in sorted(company_dir.glob("*.json")):
            blob = json.loads(path.read_text())
            stamps = [datetime.fromisoformat(t) for t in blob.get("timestamps") or []]
            month = date.fromisoformat(blob["month"] + "-01")
            start, end = month_bounds(month)
            days_in_month = (end - start).days
            in_window = [t for t in stamps if start <= t.date() < end]

            intervals = [(datetime.fromisoformat(a), datetime.fromisoformat(b))
                         for a, b in blob.get("covered_intervals") or []]
            union_days = 0.0
            if intervals:
                merged: list[list[datetime]] = []
                for lo, hi in sorted(intervals):
                    if merged and lo <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], hi)
                    else:
                        merged.append([lo, hi])
                union_days = sum((hi - lo).total_seconds()
                                 for lo, hi in merged) / 86400.0
            union_days = min(max(union_days, 0.0), float(days_in_month))

            reached = bool(blob.get("reached_month_start")) or \
                union_days >= days_in_month - 1e-9
            span = float(days_in_month) if reached else union_days
            coverage = span / days_in_month if days_in_month else 0.0
            over = (not reached) and coverage < MIN_COVERAGE
            density = (len(in_window) / span) if span > 0 and not over else None

            blob.update({
                "density_per_day": round(density, 4) if density is not None else None,
                "volume_exceeds_budget": over,
                "in_window_tweets": len(in_window),
                "observed_span_days": round(span, 4),
                "union_span_days": round(union_days, 4),
                "reached_month_start": reached,
                "coverage_fraction": round(coverage, 4),
                "min_coverage_required": MIN_COVERAGE,
                "recomputed_at": datetime.now(UTC).isoformat(),
            })
            path.write_text(json.dumps(blob, indent=1) + "\n")
            n += 1
    logger.info("twitter: recomputed %d cached months offline", n)
    return n


def aggregate() -> int:
    import pandas as pd

    records = []
    for company_dir in sorted(RAW_TWITTER.iterdir()):
        if not company_dir.is_dir():
            continue
        for path in sorted(company_dir.glob("*.json")):
            blob = json.loads(path.read_text())
            records.append({
                "company": blob["company"], "month": blob["month"], "source": "twitter",
                "query": blob["query"],
                "density_per_day": blob.get("density_per_day"),
                "volume_exceeds_budget": blob.get("volume_exceeds_budget"),
                "in_window_tweets": blob.get("in_window_tweets"),
                "reached_month_start": blob.get("reached_month_start"),
                "coverage_fraction": blob.get("coverage_fraction"),
                "observed_span_days": blob.get("observed_span_days"),
                "pages": blob.get("pages"),
                "fetched_at": blob.get("fetched_at"), "errors": blob.get("errors"),
            })
    if not records:
        logger.warning("twitter: nothing cached to aggregate")
        return 0
    df = pd.DataFrame.from_records(records).sort_values(["company", "month"])
    df.to_parquet(TWITTER_COUNTS_PARQUET, index=False)
    complete = int(df["reached_month_start"].fillna(False).sum())
    over = int(df["volume_exceeds_budget"].fillna(False).sum())
    logger.info("twitter: %d company-months (%d complete, %d tail-sampled, "
                "%d exceed the budget and carry no rate) -> %s",
                len(df), complete, len(df) - complete - over, over,
                TWITTER_COUNTS_PARQUET)
    return len(df)


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=0,
                    help="max billed calls this run. Required: this source is paid.")
    ap.add_argument("--priority", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    ap.add_argument("--anchors", type=int, default=1,
                    help="sample points inside each month; >1 spreads the page "
                         "budget across the month instead of its final hours")
    ap.add_argument("--calibrate", action="store_true",
                    help="sample a few months per company to measure page cost first")
    ap.add_argument("--aggregate-only", action="store_true")
    ap.add_argument("--recompute", action="store_true",
                    help="rebuild metrics from cached timestamps; no network, no cost")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if args.recompute:
        recompute()
    elif not args.aggregate_only:
        if args.calibrate:
            budget, months_limit = args.budget or 120, 3
        else:
            budget, months_limit = args.budget, 0
        if not budget:
            raise SystemExit(
                "refusing to run without --budget: every page is a billed call. "
                "Try --calibrate first to measure the page cost.")
        stats = await collect(call_budget=budget, max_priority=args.priority,
                              max_pages=args.max_pages, months_limit=months_limit,
                              anchors=args.anchors)
        print(f"twitter: months={stats['fetched']} calls={stats['calls']} "
              f"tail_sampled={stats['incomplete']} "
              f"unmeasurable={stats['unmeasurable']} "
              f"errored={stats['errored']} remaining={stats['remaining']} "
              f"est_cost=${stats['est_cost_usd']}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
