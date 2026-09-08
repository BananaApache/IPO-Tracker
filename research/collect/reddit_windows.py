"""Reddit post counts before filing and after listing, via redditapis.com.

**The endpoint has no date range.** `q`, `sort`, and a relative `t` bucket
(`hour|day|week|month|year|all`) are all it takes, and `t` is relative to *now* --
so `t=year` means "the last twelve months", not "the year around Lyft's 2019
S-1". There is no `from`/`to`. That is why the monthly-series attempt in
`docs`/README was abandoned for this source.

The workaround, and why it works here when it did not before: sweep `t=all`
sorted by `new`, page backwards with `after`, and bucket every post by its own
`created_utc` client-side. That was hopeless for a broad query -- one 100-post
page of `q=Instacart` spanned **2.17 days**, so reaching 2019 was impossible.
With the narrow query `"<brand>" IPO` the result set is small enough that a page
spans **~600 days**: for Lyft, page 1 covered 2024-12 to 2026-09 and page 2
covered 2023-05 to 2024-12. Reaching a 2019 window costs about five pages.

One sweep therefore serves *both* windows, which makes this cheaper per company
than the equivalent X collection, where each window needed its own query.

Two honesty mechanics:

  * **Completeness is recorded, not assumed.** A sweep that pages back past the
    start of the pre-filing window has seen everything the provider will show for
    both windows, so its counts are complete *for that listing*. A sweep that
    stops at the page cap or loses its cursor first has not, and the older window
    is then a floor. `reached_window_start` carries the distinction.
  * **Results are filtered on the brand name.** Measured precision on the raw
    response is 82/100 for Lyft -- the provider matches loosely, so posts that
    never mention the company arrive. Every post is checked against the brand and
    its aliases before counting, and the discard count is stored.

Allocation rule, fixed here rather than chosen per run: companies are processed
**most-recent listing first**. Reaching an old window costs more pages, so
recent-first maximises the number of companies completed under a fixed budget.
The cost is that a budget shortfall lands on the oldest listings; the run reports
exactly which.

Run:  uv run --group research python -m research.collect.reddit_windows --calibrate --budget 20
      uv run --group research python -m research.collect.reddit_windows --budget 240
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta

import httpx

from research.collect.nyt import slug
from research.collect.paths import DATA, RAW, ensure_dirs
from research.collect.twitter_windows import WINDOW_DAYS

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.redditapis.com/api/reddit/search"
RAW_REDDIT = RAW / "reddit_windows"
REDDIT_PARQUET = DATA / "reddit_windows.parquet"

# Documented at $0.002 per call -- more than twice the X price, so the budget
# matters more here.
COST_PER_CALL_USD = 0.002
PAGE_SIZE = 100
# Per-company page ceiling, so no single company can eat the budget. Sized from
# the measurement above: ~600 days a page means seven pages reaches back about
# eleven years, past every listing in the watchlist.
MAX_PAGES = 7


class InsufficientCredits(RuntimeError):
    """The prepaid balance is exhausted. Retrying cannot fix it."""


def mentions(post: dict, aliases: list[str]) -> bool:
    """Does the post actually name the company?

    The provider matches loosely -- 18 of 100 Lyft results never mention Lyft --
    so this is applied before counting rather than trusting the query.
    """
    text = f"{post.get('title') or ''} {post.get('text') or ''}".casefold()
    return any(a.casefold() in text for a in aliases if a)


async def sweep_company(client: httpx.AsyncClient, key: str, query: str,
                        aliases: list[str], oldest_needed: date, *,
                        max_pages: int, budget_left: int) -> dict:
    """Page backwards until `oldest_needed` is passed, the cap is hit, or the
    listing ends. Returns every kept post's timestamp."""
    kept: list[dict] = []
    seen: set[str] = set()
    oldest_seen: float | None = None
    discarded = 0
    after: str | None = None
    pages = 0
    reached = False
    errors: list[str] = []
    listing_end = False

    while pages < min(max_pages, budget_left):
        params = {"q": query, "sort": "new", "t": "all", "limit": PAGE_SIZE}
        if after:
            params["after"] = after
        resp = await client.get(SEARCH_URL, params=params,
                                headers={"Authorization": f"Bearer {key}"},
                                timeout=45.0)
        pages += 1
        if resp.status_code == 402:
            raise InsufficientCredits(resp.text[:160])
        if resp.status_code != 200:
            errors.append(f"HTTP {resp.status_code}: {resp.text[:120]}")
            break
        body = resp.json()
        posts = body.get("posts") or []
        if not posts:
            listing_end = True
            break

        oldest_on_page: float | None = None
        for post in posts:
            pid = post.get("id") or post.get("name")
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            ts = post.get("created_utc")
            if not ts:
                continue
            oldest_on_page = ts if oldest_on_page is None else min(oldest_on_page, ts)
            oldest_seen = ts if oldest_seen is None else min(oldest_seen, ts)
            if not mentions(post, aliases):
                discarded += 1
                continue
            kept.append({"id": pid, "created_utc": ts,
                         "subreddit": post.get("subreddit"),
                         "upvotes": post.get("upvotes"),
                         "comments": post.get("comments")})

        # Reached far enough back to have seen both windows in full.
        if oldest_on_page is not None and \
                datetime.fromtimestamp(oldest_on_page, UTC).date() <= oldest_needed:
            reached = True
            break
        after = body.get("after") or None
        if not after:
            # Reddit stopped issuing a cursor. Its own hint says a platform
            # cut-off and a genuine end of data are indistinguishable here, so
            # this is NOT recorded as completeness unless the window start was
            # actually passed.
            listing_end = True
            break

    return {"kept": kept, "discarded": discarded, "pages": pages,
            "reached_window_start": reached, "listing_ended": listing_end,
            # How far back the sweep actually got, counting posts that were
            # discarded for not naming the company. This is what decides
            # per-window completeness.
            "oldest_seen_ts": oldest_seen,
            "errors": errors or None}


def bucket(kept: list[dict], s1: date, listing: date,
           oldest_seen_ts: float | None) -> dict:
    """Counts per window, each with its own completeness verdict.

    Completeness is **per window, not per sweep**. `sort=new` walks backwards
    from today, so a window's count is only a count if the sweep reached back
    past that window's START. Otherwise it is a floor -- and a floor of zero
    means "never looked", not "nothing there".

    This distinction is not academic. SpaceX's first calibration sweep spent all
    three pages inside its post-listing window and never reached the pre-filing
    window at all; reporting that pre-filing count as 0 would have invented a
    finding of no anticipation.
    """
    pre_a, pre_b = s1 - timedelta(days=WINDOW_DAYS), s1
    post_a, post_b = listing, listing + timedelta(days=WINDOW_DAYS)
    pre, post = [], []
    for k in kept:
        d = datetime.fromtimestamp(k["created_utc"], UTC).date()
        if pre_a <= d < pre_b:
            pre.append(k)
        elif post_a <= d < post_b:
            post.append(k)

    oldest = (datetime.fromtimestamp(oldest_seen_ts, UTC).date()
              if oldest_seen_ts else None)
    pre_complete = bool(oldest and oldest <= pre_a)
    post_complete = bool(oldest and oldest <= post_a)
    return {
        "pre_filing_count": len(pre),
        "post_listing_count": len(post),
        "pre_filing_complete": pre_complete,
        "post_listing_complete": post_complete,
        "oldest_seen": oldest.isoformat() if oldest else None,
        "pre_filing_window": [pre_a.isoformat(), pre_b.isoformat()],
        "post_listing_window": [post_a.isoformat(), post_b.isoformat()],
        "pre_filing_ids": [k["id"] for k in pre],
        "post_listing_ids": [k["id"] for k in post],
    }


async def collect(*, budget: int, max_pages: int = MAX_PAGES,
                  limit: int = 0) -> dict:
    ensure_dirs()
    RAW_REDDIT.mkdir(parents=True, exist_ok=True)

    from research.collect.config import get_research_settings
    from research.collect.edgar_enrich import read_watchlist_df

    s = get_research_settings()
    if not s.reddit_key:
        raise SystemExit("REDDITAPI_KEY is not set.")

    wl = read_watchlist_df()
    rows = [r for r in wl[wl["tier_b"]].to_dict("records")
            if str(r.get("registration_filed_at"))[:4].isdigit()
            and str(r.get("listing_date"))[:4].isdigit()]
    # Most-recent listing first: an old window costs more pages, so this
    # maximises completed companies under a fixed budget.
    rows.sort(key=lambda r: str(r.get("listing_date")), reverse=True)
    if limit:
        rows = rows[:limit]

    todo = [r for r in rows
            if not (RAW_REDDIT / f"{slug(r['company'])}.json").exists()]
    logger.info("reddit_windows: %d companies to sweep, budget %d calls "
                "(~$%.2f at $%.3f/call). Cap %d pages each.",
                len(todo), budget, budget * COST_PER_CALL_USD,
                COST_PER_CALL_USD, max_pages)

    stats = {"companies": 0, "calls": 0, "complete": 0, "partial": 0,
             "errored": 0, "remaining": 0}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for row in todo:
            left = budget - stats["calls"]
            if left < 1:
                stats["remaining"] = len(todo) - stats["companies"]
                logger.info("reddit_windows: budget spent; %d companies left "
                            "(the oldest listings)", stats["remaining"])
                break

            s1 = date.fromisoformat(str(row["registration_filed_at"])[:10])
            listing = date.fromisoformat(str(row["listing_date"])[:10])
            oldest_needed = s1 - timedelta(days=WINDOW_DAYS)
            brand = row["company"].split("(")[0].strip()
            aliases = [a for a in str(row.get("aliases") or brand).split("|") if a]
            query = f'"{brand}" IPO'

            try:
                sw = await sweep_company(client, s.reddit_key, query, aliases,
                                         oldest_needed, max_pages=max_pages,
                                         budget_left=left)
            except InsufficientCredits as exc:
                logger.error("reddit_windows: out of credits (%s). Stopping; "
                             "%d companies uncollected, nothing cached for them.",
                             str(exc)[:100], len(todo) - stats["companies"])
                stats["remaining"] = len(todo) - stats["companies"]
                break

            stats["calls"] += sw["pages"]
            if sw.get("errors"):
                logger.warning("%-24s errors, not cached: %s", row["company"][:24],
                               str(sw["errors"][0])[:70])
                stats["errored"] += 1
                continue

            b = bucket(sw["kept"], s1, listing, sw.get("oldest_seen_ts"))
            complete = b["pre_filing_complete"] and b["post_listing_complete"]
            (RAW_REDDIT / f"{slug(row['company'])}.json").write_text(json.dumps({
                "company": row["company"],
                "query": query,
                "aliases": aliases,
                "s1_date": s1.isoformat(),
                "listing_date": listing.isoformat(),
                "oldest_needed": oldest_needed.isoformat(),
                "fetched_at": datetime.now(UTC).isoformat(),
                "service": "redditapis.com (third-party scraper, not the official Reddit API)",
                "note": ("No date-range parameter exists on this endpoint; t is "
                         "relative to now. Windows are bucketed client-side from "
                         "created_utc."),
                "pages": sw["pages"],
                "kept_posts": len(sw["kept"]),
                "discarded_no_mention": sw["discarded"],
                "reached_window_start": bool(sw["reached_window_start"]),
                "oldest_seen_ts": sw.get("oldest_seen_ts"),
                "listing_ended": sw["listing_ended"],
                "posts": sw["kept"],
                **b,
            }, indent=1) + "\n")
            stats["companies"] += 1
            stats["complete"] += 1 if complete else 0
            stats["partial"] += 0 if complete else 1
            pre_s = (str(b["pre_filing_count"]) if b["pre_filing_complete"]
                     else f'>={b["pre_filing_count"]}?')
            post_s = (str(b["post_listing_count"]) if b["post_listing_complete"]
                      else f'>={b["post_listing_count"]}?')
            logger.info("%-24s pre %-6s post %-6s back to %s  (%d pages, %d kept, "
                        "%d discarded)%s", row["company"][:24], pre_s, post_s,
                        b["oldest_seen"] or "?", sw["pages"], len(sw["kept"]),
                        sw["discarded"], "" if complete else "  INCOMPLETE")
    stats["est_cost_usd"] = round(stats["calls"] * COST_PER_CALL_USD, 4)
    return stats


def aggregate() -> int:
    import pandas as pd

    records = []
    for path in sorted(RAW_REDDIT.glob("*.json")):
        b = json.loads(path.read_text())
        records.append({k: b.get(k) for k in (
            "company", "query", "s1_date", "listing_date", "pre_filing_count",
            "post_listing_count", "pre_filing_complete", "post_listing_complete",
            "oldest_seen", "pages", "kept_posts", "discarded_no_mention",
            "reached_window_start", "listing_ended", "fetched_at")})
    if not records:
        logger.warning("reddit_windows: nothing cached to aggregate")
        return 0
    df = pd.DataFrame.from_records(records).sort_values("company")
    df["counts_complete"] = (df["pre_filing_complete"].astype(bool)
                             & df["post_listing_complete"].astype(bool))
    # Same interval test as the X windows: a floor vs a floor settles nothing,
    # and an incomplete count of 0 means "never looked", not "nothing there".
    inf = float("inf")
    pre_hi = df["pre_filing_count"].where(df["pre_filing_complete"].astype(bool), inf)
    post_hi = df["post_listing_count"].where(
        df["post_listing_complete"].astype(bool), inf)
    df["post_exceeds_pre"] = df["post_listing_count"] > pre_hi
    df["pre_exceeds_post"] = df["pre_filing_count"] > post_hi
    df["direction_established"] = df["post_exceeds_pre"] | df["pre_exceeds_post"]
    df.to_parquet(REDDIT_PARQUET, index=False)
    logger.info("reddit_windows: %d companies -> %s", len(df), REDDIT_PARQUET)
    logger.info("    both windows complete   %d", int(df["counts_complete"].sum()))
    logger.info("    direction established   %d",
                int(df["direction_established"].sum()))
    return len(df)


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=0,
                    help="hard ceiling on billed calls for this run")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--calibrate", action="store_true",
                    help="three companies only, to measure page cost")
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if not args.aggregate_only:
        if not args.budget:
            raise SystemExit("refusing to run without --budget: each page is billed.")
        stats = await collect(budget=args.budget, max_pages=args.max_pages,
                              limit=3 if args.calibrate else 0)
        print(f"reddit_windows: companies={stats['companies']} "
              f"calls={stats['calls']} complete={stats['complete']} "
              f"partial={stats['partial']} errored={stats['errored']} "
              f"remaining={stats['remaining']} est_cost=${stats['est_cost_usd']}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
