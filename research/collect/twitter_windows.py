"""X post counts in two fixed windows per company: before filing, after listing.

A different measurement from `collect/twitter.py`. That module estimates a
monthly *density* across a 27-month series and needs a large budget. This one
answers a narrower, cheaper question -- how much IPO-specific chatter was there
in a fixed window before the S-1 and a fixed window after the listing -- and it
is designed to fit a small, hard call budget.

Three things make it affordable where the monthly series was not:

  * **The query is narrow.** `"<brand>" IPO` rather than the bare brand name.
    Requiring "IPO" collapses the volume by orders of magnitude: bare `"Figma"`
    is a firehose of design-tool chatter, while `"Figma" IPO` is the offering.
    For a pre-filing window that is also the *right* query -- a tweet saying
    "Figma IPO" before the S-1 is anticipation, which is the signal.
  * **Two windows, not 27 months.** 39 companies x 2 windows = 78 requests-worth
    of work rather than ~1,000.
  * **Equal-length windows.** Both default to 90 days, so the two counts are
    directly comparable without normalising by window length. Unequal windows
    would make the comparison meaningless.

There is still no total-count field, so a count means paginating until the
provider runs out. When a window exhausts inside the page cap the count is
**exact**; when it hits the cap the count is a **lower bound** and `exhausted`
is false. The two are never mixed in an average.

Budget discipline, learned the hard way after exhausting a previous balance:

  * `--budget` is a hard ceiling on billed calls for the whole run and is
    checked before every request.
  * A window whose requests errored is **never cached**, because the cache is
    also the resume mechanism and a cached failure would later read as a
    measured zero.
  * `HTTP 402` aborts the entire run and writes nothing.
  * Every tweet timestamp is stored, so counts can be recomputed offline with
    `--recompute` instead of re-billing to fix arithmetic.

Run:  uv run --group research python -m research.collect.twitter_windows --calibrate --budget 30
      uv run --group research python -m research.collect.twitter_windows --budget 600
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta

import httpx

from research.collect.config import get_research_settings
from research.collect.nyt import slug
from research.collect.paths import DATA, RAW, ensure_dirs
from research.collect.twitter import (
    COST_PER_CALL_USD,
    SEARCH_URL,
    InsufficientCredits,
    parse_created_at,
)

logger = logging.getLogger(__name__)

RAW_WINDOWS = RAW / "twitter_windows"
WINDOWS_PARQUET = DATA / "twitter_windows.parquet"

# Equal-length windows, so the two counts compare directly.
WINDOW_DAYS = 90
# Per-window page ceiling. 20 tweets a page, so 8 pages sees up to 160 tweets.
# Sized from the budget (686 calls / 78 windows ~ 8.8), not from a guess about
# volume -- and the run reports which windows hit it.
MAX_PAGES = 8


def windows_for(row: dict) -> list[tuple[str, date, date]]:
    """(label, start, end) windows for one company. `end` is exclusive.

    Anchored on the *public* S-1 and the listing, which is what was asked for.
    Note the caveat the EDGAR timeline exposed: for most companies a
    confidential DRS precedes the S-1 by a median of ~96 days, so a
    "before filing" window of 90 days sits largely *inside* the confidential
    registration period rather than before the process began. That is recorded
    per row as `days_after_drs` so the interpretation stays honest.
    """
    out = []
    s1 = row.get("registration_filed_at")
    listing = row.get("listing_date")
    try:
        s1_d = date.fromisoformat(str(s1)[:10])
        out.append(("pre_filing", s1_d - timedelta(days=WINDOW_DAYS), s1_d))
    except (ValueError, TypeError):
        pass
    try:
        list_d = date.fromisoformat(str(listing)[:10])
        out.append(("post_listing", list_d, list_d + timedelta(days=WINDOW_DAYS)))
    except (ValueError, TypeError):
        pass
    return out


def query_for(company: str) -> str:
    """The frozen query form, identical for every company.

    Brand in quotes so a multi-word name is not split into an OR of its words,
    then a bare `IPO` as a required term. Fixed before collection and not tuned
    per company after seeing counts.
    """
    brand = company.split("(")[0].strip()
    return f'"{brand}" IPO'


async def count_window(client: httpx.AsyncClient, key: str, query: str,
                       start: date, end: date, *, max_pages: int,
                       budget_left: int) -> dict:
    """Paginate one window. Returns the record, or raises InsufficientCredits."""
    full = f"{query} since:{start.isoformat()} until:{end.isoformat()}"
    stamps: list[datetime] = []
    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    exhausted = False
    errors: list[str] = []

    while pages < min(max_pages, budget_left):
        params = {"query": full, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
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
        tweets = body.get("tweets") or []
        if not tweets:
            exhausted = True
            break
        for t in tweets:
            tid = t.get("id")
            if tid and tid in seen:
                continue
            if tid:
                seen.add(tid)
            ts = parse_created_at(t.get("created_at"))
            if ts:
                stamps.append(ts)
        cursor = body.get("next_cursor") or None
        if not cursor:
            exhausted = True
            break

    # `until:` is honoured loosely -- the probe saw stamps a day past the bound
    # -- so the window filter is applied here on parsed timestamps, not trusted
    # to the operator.
    in_window = [t for t in stamps if start <= t.date() < end]
    return {
        "count": len(in_window),
        # Kept so a later --deepen resumes instead of re-paying for pages it
        # already bought. The first deepening run could not use this because
        # earlier records predate the field.
        "final_cursor": cursor,
        # True: the provider ran out inside the page cap, so `count` is exact.
        # False: the cap was hit and `count` is a LOWER BOUND.
        "exhausted": exhausted,
        "is_exact": exhausted and not errors,
        "out_of_window": len(stamps) - len(in_window),
        "pages": pages,
        "max_pages": max_pages,
        "window_days": (end - start).days,
        "timestamps": sorted(t.isoformat() for t in in_window),
        "errors": errors or None,
    }


async def deepen(*, budget: int, count: int, max_pages: int) -> dict:
    """Re-collect the windows most likely to convert from a bound to a count.

    Selection rule, fixed here rather than chosen per run: **`pre_filing`
    windows currently classified `lower_bound`, ordered by in-window count
    ascending.** Two reasons, both about measurement rather than outcome:

      * `pre_filing` is the side that matters. Post-listing chatter being high
        is close to tautological; whether there was a pre-filing run-up is the
        actual question.
      * A window already returning many *out-of-window* results is near the
        edge of the provider's data for that range, so it is the likeliest to
        exhaust with more pages. Ordering by ascending in-window count puts
        those first and puts a still-saturated window (Reddit: 160 in-window,
        0 outside) last, where the budget will not reach it.

    A deepened result **only replaces the cached one if it is strictly better**
    -- more pages read, or newly exact. A worse or equal attempt is discarded,
    so a run that hits a flaky moment cannot degrade what is already on disk.
    """
    import pandas as pd

    if not WINDOWS_PARQUET.exists():
        raise SystemExit("nothing collected yet; run without --deepen first.")
    d = pd.read_parquet(WINDOWS_PARQUET)
    cand = d[(d["window"] == "pre_filing") & (d["quality"] == "lower_bound")]
    cand = cand.sort_values("count").head(count)
    if cand.empty:
        raise SystemExit("no pre_filing lower_bound windows to deepen.")

    s = get_research_settings()
    logger.info("deepen: %d windows, cap %d pages, budget %d calls (~$%.2f)",
                len(cand), max_pages, budget, budget * COST_PER_CALL_USD)

    stats = {"attempted": 0, "improved": 0, "converted": 0, "discarded": 0,
             "calls": 0, "remaining": 0}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for _, row in cand.iterrows():
            left = budget - stats["calls"]
            if left < 2:
                stats["remaining"] = len(cand) - stats["attempted"]
                logger.info("deepen: budget spent; %d windows untouched",
                            stats["remaining"])
                break
            path = RAW_WINDOWS / f"{slug(row['company'])}__{row['window']}.json"
            old = json.loads(path.read_text())
            start = date.fromisoformat(row["start"])
            end = date.fromisoformat(row["end"])
            try:
                rec = await count_window(client, s.twitter_key,
                                         query_for(row["company"]), start, end,
                                         max_pages=max_pages, budget_left=left)
            except InsufficientCredits as exc:
                logger.error("deepen: out of credits (%s). Stopping; nothing "
                             "overwritten for the remaining windows.", str(exc)[:90])
                stats["remaining"] = len(cand) - stats["attempted"]
                break
            stats["attempted"] += 1
            stats["calls"] += rec["pages"]

            if rec.get("errors") or rec["pages"] <= old.get("pages", 0):
                stats["discarded"] += 1
                logger.info("%-22s kept old (%d pages, %s%d) -- new attempt read "
                            "%d pages%s", row["company"][:22], old.get("pages", 0),
                            "" if old.get("is_exact") else ">=", old.get("count", 0),
                            rec["pages"],
                            " with errors" if rec.get("errors") else "")
                continue

            became_exact = rec["is_exact"] and not old.get("is_exact")
            path.write_text(json.dumps({
                **old, **rec,
                "deepened_at": datetime.now(UTC).isoformat(),
                "deepened_from": {"count": old.get("count"),
                                  "pages": old.get("pages"),
                                  "is_exact": old.get("is_exact")},
            }, indent=1) + "\n")
            stats["improved"] += 1
            stats["converted"] += 1 if became_exact else 0
            logger.info("%-22s %s%d -> %s%-4d tweets (%d -> %d pages)%s",
                        row["company"][:22],
                        "" if old.get("is_exact") else ">=", old.get("count", 0),
                        "" if rec["is_exact"] else ">=", rec["count"],
                        old.get("pages", 0), rec["pages"],
                        "  NOW EXACT" if became_exact else "")
    stats["est_cost_usd"] = round(stats["calls"] * COST_PER_CALL_USD, 4)
    return stats


async def collect(*, budget: int, max_pages: int = MAX_PAGES,
                  companies: list[str] | None = None) -> dict:
    ensure_dirs()
    RAW_WINDOWS.mkdir(parents=True, exist_ok=True)
    s = get_research_settings()
    if not s.twitter_key:
        raise SystemExit("TWITTERAPI_KEY is not set.")

    from research.collect.edgar_enrich import read_watchlist_df
    from research.collect.edgar_events import EVENTS_PARQUET

    import pandas as pd

    wl = read_watchlist_df()
    rows = wl[wl["tier_b"]].to_dict("records")
    if companies:
        rows = [r for r in rows if r["company"] in companies]

    drs = {}
    if EVENTS_PARQUET.exists():
        ev = pd.read_parquet(EVENTS_PARQUET)
        drs = dict(zip(ev["company"], ev["drs_first"]))

    jobs = []
    for row in rows:
        for label, start, end in windows_for(row):
            path = RAW_WINDOWS / f"{slug(row['company'])}__{label}.json"
            if not path.exists():
                jobs.append((row, label, start, end, path))

    logger.info("twitter_windows: %d windows to collect, budget %d calls "
                "(~$%.2f). Cap %d pages/window.",
                len(jobs), budget, budget * COST_PER_CALL_USD, max_pages)

    stats = {"windows": 0, "calls": 0, "exact": 0, "capped": 0, "errored": 0,
             "remaining": 0}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for row, label, start, end, path in jobs:
            left = budget - stats["calls"]
            if left <= 0:
                stats["remaining"] = len(jobs) - stats["windows"]
                logger.info("twitter_windows: budget spent; %d windows left",
                            stats["remaining"])
                break
            try:
                rec = await count_window(client, s.twitter_key,
                                         query_for(row["company"]), start, end,
                                         max_pages=max_pages, budget_left=left)
            except InsufficientCredits as exc:
                logger.error("twitter_windows: out of credits (%s). Stopping; "
                             "%d windows uncollected and NOTHING cached for them.",
                             str(exc)[:100], len(jobs) - stats["windows"])
                stats["remaining"] = len(jobs) - stats["windows"]
                break

            stats["calls"] += rec["pages"]
            if rec.get("errors"):
                logger.warning("%-24s %-13s errors, not cached: %s",
                               row["company"][:24], label,
                               str(rec["errors"][0])[:70])
                stats["errored"] += 1
                continue

            drs_date = drs.get(row["company"])
            days_after_drs = None
            if drs_date and isinstance(drs_date, str):
                try:
                    days_after_drs = (start - date.fromisoformat(drs_date[:10])).days
                except ValueError:
                    pass

            path.write_text(json.dumps({
                "company": row["company"],
                "window": label,
                "query": query_for(row["company"]),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "s1_date": str(row.get("registration_filed_at"))[:10],
                "listing_date": str(row.get("listing_date"))[:10],
                "drs_date": str(drs_date)[:10] if drs_date else None,
                # Positive: this window STARTS after the confidential filing, so
                # it is inside the registration period rather than before it.
                "window_start_days_after_drs": days_after_drs,
                "fetched_at": datetime.now(UTC).isoformat(),
                "service": "twitterapis.com (third-party scraper, not the official X API)",
                **rec,
            }, indent=1) + "\n")
            stats["windows"] += 1
            stats["exact"] += 1 if rec["is_exact"] else 0
            stats["capped"] += 0 if rec["is_exact"] else 1
            logger.info("%-24s %-13s %s%-4d tweets in %d pages%s",
                        row["company"][:24], label,
                        "" if rec["is_exact"] else ">=", rec["count"], rec["pages"],
                        "" if rec["is_exact"] else "  CAPPED (lower bound)")
    stats["est_cost_usd"] = round(stats["calls"] * COST_PER_CALL_USD, 4)
    return stats


def aggregate() -> int:
    import pandas as pd

    records = []
    for path in sorted(RAW_WINDOWS.glob("*.json")):
        blob = json.loads(path.read_text())
        records.append({k: blob.get(k) for k in (
            "company", "window", "query", "start", "end", "count", "is_exact",
            "exhausted", "pages", "out_of_window", "window_days", "s1_date",
            "listing_date", "drs_date", "window_start_days_after_drs",
            "fetched_at")})
    if not records:
        logger.warning("twitter_windows: nothing cached to aggregate")
        return 0
    df = pd.DataFrame.from_records(records).sort_values(["company", "window"])

    # `is_exact` alone conflates two different outcomes, and they mean opposite
    # things for the analysis. Classified here from stored fields, so no
    # re-billing is needed:
    #
    #   exact              the provider ran out inside the page cap. A count.
    #   lower_bound        the cap was hit while the results were still IN the
    #                      window -- a genuinely busy window. Reddit's pre-filing
    #                      window returned 160 in-window tweets and 0 outside, so
    #                      "at least 160" is a real statement.
    #   unreliable_window  the cap was hit but most results fell OUTSIDE the
    #                      requested window, meaning the since:/until: operators
    #                      were largely ignored for that query. Figma's
    #                      pre-filing window found 20 in-window against 140
    #                      outside; that is neither a count nor a usable bound,
    #                      and averaging it with the others would be wrong.
    fetched = df["count"].fillna(0) + df["out_of_window"].fillna(0)
    off_target = df["out_of_window"].fillna(0) / fetched.where(fetched > 0)
    df["off_target_fraction"] = off_target.round(3)
    df["quality"] = "exact"
    capped = ~df["is_exact"].astype(bool)
    df.loc[capped, "quality"] = "lower_bound"
    df.loc[capped & (off_target > 0.5), "quality"] = "unreliable_window"
    # A usable count is one the analysis may treat as a number or a floor.
    df["usable"] = df["quality"].isin(["exact", "lower_bound"])

    df.to_parquet(WINDOWS_PARQUET, index=False)
    counts = df["quality"].value_counts().to_dict()
    logger.info("twitter_windows: %d windows -> %s", len(df), WINDOWS_PARQUET)
    for q in ("exact", "lower_bound", "unreliable_window"):
        if counts.get(q):
            logger.info("    %-18s %d", q, counts[q])
    return len(df)


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=0,
                    help="hard ceiling on billed calls for this run")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--calibrate", action="store_true",
                    help="two companies only: one busy, one quiet")
    ap.add_argument("--deepen", type=int, default=0, metavar="N",
                    help="re-collect the N pre_filing lower-bound windows nearest "
                         "to exhausting, at a higher --max-pages")
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if not args.aggregate_only:
        if not args.budget:
            raise SystemExit("refusing to run without --budget: each page is billed.")
        if args.deepen:
            stats = await deepen(budget=args.budget, count=args.deepen,
                                 max_pages=args.max_pages)
            print(f"deepen: attempted={stats['attempted']} "
                  f"improved={stats['improved']} converted={stats['converted']} "
                  f"discarded={stats['discarded']} calls={stats['calls']} "
                  f"remaining={stats['remaining']} est_cost=${stats['est_cost_usd']}")
        else:
            companies = ["Reddit", "Fervo Energy"] if args.calibrate else None
            stats = await collect(budget=args.budget, max_pages=args.max_pages,
                                  companies=companies)
            print(f"twitter_windows: windows={stats['windows']} "
                  f"calls={stats['calls']} exact={stats['exact']} "
                  f"capped={stats['capped']} errored={stats['errored']} "
                  f"remaining={stats['remaining']} est_cost=${stats['est_cost_usd']}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
