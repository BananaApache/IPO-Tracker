"""NYT Article Search: monthly article counts per Tier B company.

The primary news signal, and the only one of the two news sources in the module
brief that survived measurement -- GNews grants 30 days of history on the free
plan, so it cannot see a pre-S-1 window at all. See `data/source_probe.json`.

Three things measured on the live API shape this collector, all of which differ
from the brief:

  * The hit count is at `response.metadata.hits`. There is no `response.meta`.
  * Fielded `fq` returns 0 for every field tried, so there is no entity
    constraint available and the query is `q` only. The per-company query form
    is frozen in `collect/aliases.py` before any counting.
  * The limits are ~5 requests/minute and ~500/day. The minute limit is spacing;
    the daily limit is a hard stop this collector enforces itself, because a
    27-month window across the watchlist exceeds one day's quota and the run has
    to be able to end and resume tomorrow.

Resumability is per company-month: each window's response is written to
`data/raw/nyt/<slug>/<YYYY-MM>.json` and a window already on disk is never
re-requested. A run that dies at hour six resumes rather than restarting.

Run:  uv run --group research python -m research.collect.nyt
      uv run --group research python -m research.collect.nyt --aggregate-only
"""

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from backend.http import RetryingClient
from research.collect.config import get_research_settings
from research.collect.paths import (
    NYT_COUNTS_PARQUET,
    RAW_NYT,
    WATCHLIST_CSV,
    ensure_dirs,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"

# The window the module brief specifies: registration minus 24 months through
# listing plus 3 months.
MONTHS_BEFORE = 24
MONTHS_AFTER = 3


class DailyCapReached(RuntimeError):
    """The provider's daily quota is spent. Not an error -- a stopping point."""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def month_starts(first: date, last: date) -> list[date]:
    out, cur = [], first.replace(day=1)
    while cur <= last:
        out.append(cur)
        cur = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
               else cur.replace(month=cur.month + 1))
    return out


def month_end(d: date) -> date:
    nxt = (d.replace(year=d.year + 1, month=1) if d.month == 12
           else d.replace(month=d.month + 1))
    return nxt - timedelta(days=1)


def add_months(d: date, n: int) -> date:
    total = (d.year * 12 + d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def load_tier_b(max_priority: int = 9) -> list[dict[str, str]]:
    """Tier B rows at or above `max_priority`, in the watchlist's own order.

    The order is fixed in watchlist.csv by collect.edgar_enrich and is not
    re-derived here: collection order must not depend on which script ran.
    """
    if not WATCHLIST_CSV.exists():
        raise SystemExit("no watchlist.csv; run collect.edgar_enrich first.")
    with WATCHLIST_CSV.open(newline="") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r.get("tier_b", "").strip() == "True"
                and int(r.get("tier_b_priority") or 9) <= max_priority]
    if not rows:
        raise SystemExit(f"watchlist.csv has no Tier B rows at priority <= {max_priority}.")
    return rows


def window_for(row: dict[str, str]) -> tuple[date, date] | None:
    """(first month, last month) for one company, or None if unanchored."""
    reg = row.get("registration_filed_at") or ""
    listed = row.get("listing_filed_at") or row.get("finnhub_date") or ""
    try:
        reg_d = date.fromisoformat(reg[:10])
    except ValueError:
        return None
    try:
        list_d = date.fromisoformat(listed[:10])
    except ValueError:
        # No listing evidence: still collect the pre-filing window and three
        # months past registration, rather than skipping the row entirely.
        list_d = reg_d
    return add_months(reg_d, -MONTHS_BEFORE), add_months(list_d, MONTHS_AFTER)


async def collect(*, call_budget: int, max_priority: int = 9) -> dict[str, int]:
    ensure_dirs()
    s = get_research_settings()
    if not s.nyt_key:
        raise SystemExit("NYTAPI_KEY is not set.")

    rows = load_tier_b(max_priority)
    # Interleave companies rather than finishing one before starting the next.
    # With a quota smaller than the job, finishing company 1 through 8 and
    # leaving 9 through 20 with nothing produces a dataset that cannot be
    # analysed at all; one partial month per company everywhere can be.
    jobs: list[tuple[dict[str, str], date]] = []
    per_company: dict[str, list[date]] = {}
    for row in rows:
        win = window_for(row)
        if win is None:
            logger.warning("%s: no registration date; skipped", row["company"])
            continue
        per_company[row["company"]] = month_starts(*win)
    for i in range(max((len(v) for v in per_company.values()), default=0)):
        for row in rows:
            months = per_company.get(row["company"]) or []
            if i < len(months):
                jobs.append((row, months[i]))

    todo = []
    for row, m in jobs:
        path = RAW_NYT / slug(row["company"]) / f"{m:%Y-%m}.json"
        if not path.exists():
            todo.append((row, m, path))

    logger.info("nyt: %d company-months in window, %d already cached, %d to fetch "
                "(budget %d)", len(jobs), len(jobs) - len(todo), len(todo), call_budget)

    client = RetryingClient(user_agent=s.sec_user_agent, per_second=s.nyt_per_second,
                            max_retries=4, base_backoff=20.0, max_backoff=120.0)
    stats = {"fetched": 0, "skipped": len(jobs) - len(todo), "remaining": 0}
    try:
        for row, m, path in todo:
            if stats["fetched"] >= call_budget:
                stats["remaining"] = len(todo) - stats["fetched"]
                logger.info("nyt: call budget reached; %d company-months left",
                            stats["remaining"])
                break
            params = {
                "q": row["nyt_query"],
                "begin_date": m.strftime("%Y%m%d"),
                "end_date": month_end(m).strftime("%Y%m%d"),
                "api-key": s.nyt_key,
            }
            try:
                payload = await client.get_json(SEARCH_URL, params=params)
            except Exception as exc:
                # A 429 that survives retries means the daily cap, not the
                # per-minute one. Stop the run cleanly so the cache stays
                # consistent and tomorrow's run resumes.
                if "429" in str(exc):
                    raise DailyCapReached(str(exc)) from exc
                raise
            resp = payload.get("response") or {}
            hits = (resp.get("metadata") or {}).get("hits")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "company": row["company"],
                "query": row["nyt_query"],
                "month": f"{m:%Y-%m}",
                "begin_date": params["begin_date"],
                "end_date": params["end_date"],
                "fetched_at": datetime.now(UTC).isoformat(),
                "hits": hits,
                # The first page of docs arrives in the same response as the
                # count, so keeping it costs no quota and is what makes a
                # hand-check of precision possible at all. Counting never
                # paginates: pagination is capped and `hits` is already the total.
                "docs": resp.get("docs") or [],
            }, indent=1) + "\n")
            stats["fetched"] += 1
            logger.info("nyt: %-26s %s -> %s hits", row["company"][:26], f"{m:%Y-%m}", hits)
    except DailyCapReached as exc:
        logger.warning("nyt: daily cap reached (%s); resume tomorrow", str(exc)[:120])
        stats["remaining"] = len(todo) - stats["fetched"]
    finally:
        await client.aclose()
    return stats


def aggregate() -> int:
    """Build the monthly-count table from cached responses only. No network."""
    import pandas as pd

    records = []
    for company_dir in sorted(RAW_NYT.iterdir()):
        if not company_dir.is_dir():
            continue
        for path in sorted(company_dir.glob("*.json")):
            blob = json.loads(path.read_text())
            records.append({
                "company": blob["company"],
                "month": blob["month"],
                "source": "nyt",
                "query": blob["query"],
                "count": blob.get("hits"),
                "fetched_at": blob.get("fetched_at"),
                "sampled_docs": len(blob.get("docs") or []),
            })
    if not records:
        logger.warning("nyt: nothing cached to aggregate")
        return 0
    df = pd.DataFrame.from_records(records).sort_values(["company", "month"])
    df.to_parquet(NYT_COUNTS_PARQUET, index=False)
    logger.info("nyt: %d company-months -> %s", len(df), NYT_COUNTS_PARQUET)
    return len(df)


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=None,
                    help="max API calls this run (default: the daily cap)")
    ap.add_argument("--priority", type=int, default=9,
                    help="collect only Tier B rows at this priority or better")
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if not args.aggregate_only:
        budget = args.budget if args.budget is not None else get_research_settings().nyt_daily_cap
        stats = await collect(call_budget=budget, max_priority=args.priority)
        print(f"nyt: fetched={stats['fetched']} cached={stats['skipped']} "
              f"remaining={stats['remaining']}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
