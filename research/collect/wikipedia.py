"""Wikipedia pageviews: a dense, free, official attention series.

Why this source, when `docs/google-trends-decision.md` already rejected a
search-interest index: the objections there were that Trends returns a
**normalized** index (relative to its own query and window), that it is
**sampled** (the same query twice gives different numbers), and that it is not
discrete items. Pageviews share only the third property. They are **absolute
counts** with a published methodology and they are **deterministic** -- the same
request returns the same numbers -- so they can be cited, recomputed and
audited. Trends cannot.

That third objection does still hold: a pageview total is an aggregate, not a
list of events with ids and authors. So this does not fit the deployed
`SourceAdapter`/`RawMention` interface and is not offered to it. It fits
`research/`, which already works in monthly aggregates.

What it buys, measured: keyless, no API key of any kind, monthly granularity in
one request per article, and coverage from **2015-07-01** to today -- deeper than
the study window needs. There is no per-call cost and no meaningful rate limit,
which is the real unlock: this can run over a whole cohort rather than the dozen
companies a quota-limited news API allows.

**The trap, and how it is handled.** The API *omits* months before the article
existed rather than returning zero. CoreWeave's article was created 2024-06-20;
a request from 2015 simply starts in June 2024. Treating those absent months as
zero attention would be wrong -- and worse, it would manufacture exactly the
"attention rises before the filing" shape this module is testing for, because an
article created at IPO time makes every earlier month look quiet. So the article
creation date is fetched separately and every monthly row carries
`article_existed`; months before creation are **null, never zero**.

Article creation is itself an attention event and is recorded as one: CoreWeave's
article appeared three days after its confidential DRS filing.

Run:  uv run --group research python -m research.collect.wikipedia --cohort tier_b
      uv run --group research python -m research.collect.wikipedia --cohort tier_a_sample
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime
from typing import Any

from backend.http import RetryingClient
from research.collect.config import get_research_settings
from research.collect.nyt import slug
from research.collect.paths import DATA, RAW, ensure_dirs

logger = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "en.wikipedia/all-access/user/{title}/monthly/{start}/{end}")

# Pageviews begin here. Requesting earlier is harmless but returns nothing, and
# the boundary has to be recorded so an empty early month is not read as quiet.
COVERAGE_START = date(2015, 7, 1)

RAW_WIKI = RAW / "wikipedia"
WIKI_PARQUET = DATA / "wikipedia_monthly.parquet"
WIKI_RESOLUTION_CSV = DATA / "wikipedia_resolution.csv"

# Titles that search resolves wrongly or ambiguously. Fixed here before any
# counting, for the same reason the NYT query forms are: an entity mapping
# chosen after seeing the series is a free parameter.
#
# Every entry was checked by opening the article. The `notes` column in the
# resolution CSV records how each row was decided.
TITLE_OVERRIDES: dict[str, str] = {
    "Circle Internet": "Circle (company)",
    # "Chime Financial" exists but is a 2025 stub with no traffic; the article
    # readers actually visit is "Chime (company)" (~11k/month vs none).
    "Chime": "Chime (company)",
    "Figure": "Figure Technologies",
    # "Bullish (company)" does not exist; the article is at the bare title.
    "Bullish": "Bullish",
    "Unity": "Unity Technologies",
    "Arm Holdings": "Arm Holdings",
    "Instacart (Maplebear)": "Instacart",
    "Nu Holdings (Nubank)": "Nubank",
    "Jersey Mike's Subs": "Jersey Mike's",
    "StubHub": "StubHub",
    "Slack": "Slack Technologies",
    "Snowflake": "Snowflake Inc.",
    "Zoom": "Zoom Communications",
    "Bumble": "Bumble (app)",
    "Klarna": "Klarna",
    # "Reddit, Inc." is a 3-views/month stub. The article people read is
    # "Reddit" (~210k/month). That conflates interest in the website with
    # interest in the company, which is a real limitation and is stated in the
    # README -- but a stub with no traffic measures nothing at all.
    "Reddit": "Reddit",
    # Search resolved these to the wrong subject entirely -- caught by the
    # resolution log, which is why it exists.
    "Robinhood": "Robinhood Markets",       # was "Robin Hood (disambiguation)"
    "Peloton": "Peloton Interactive",       # "Peloton" is the cycling term
    "Medline": "Medline Inc.",              # "Medline" is the NIH bibliographic database
    # Verified correct as the bare title, listed so it is on the record as checked:
    # "Palantir" is the company ("Palantir" with an accent is the Tolkien object),
    # and "Uber" is the company rather than the German prefix.
    "Palantir": "Palantir",
    "Uber": "Uber",
}


# Companies where the automatic validity floor is wrong, with a hand-verified
# month instead. Kept deliberately small and each entry justified: this is an
# override on a data-quality rule, so an unexplained entry here would be a free
# parameter.
#
# The automatic floor takes the later of (article creation, first EDGAR filing).
# That is right when a title was repurposed -- "Bullish" dates from 2005, when it
# held the market term, and was rewritten for the 2021 crypto exchange -- and
# wrong for a long-established foreign company whose first US filing is recent.
# The two cases look identical to any heuristic: old article, recent filing. So
# the handful that matter are checked by opening the article history.
HAND_VERIFIED_FLOOR: dict[str, str] = {
    # Founded 2005, article created 2010-05-10 and about Klarna throughout. Its
    # first US EDGAR trace is 2023 only because it is a foreign private issuer,
    # so the EDGAR floor would discard thirteen years of genuine attention.
    "Klarna": "2015-07",   # i.e. no clipping; pageview coverage starts here
}


async def resolve_title(client: RetryingClient, company: str) -> dict[str, Any]:
    """Company name -> Wikipedia article title, with the evidence kept.

    Override first, then an exact-title probe, then search. The candidate list
    is stored either way so a wrong mapping is findable rather than invisible.
    """
    brand = company.split("(")[0].strip()

    if company in TITLE_OVERRIDES:
        return {"company": company, "title": TITLE_OVERRIDES[company],
                "method": "curated override", "candidates": [], "notes":
                "brand is ambiguous or differs from the article title"}

    # Exact title, following redirects. A hit here is the strongest evidence.
    payload = await client.get_json(API, params={
        "action": "query", "titles": brand, "redirects": 1, "format": "json"})
    pages = (payload.get("query") or {}).get("pages") or {}
    for page in pages.values():
        if "missing" not in page and page.get("title"):
            return {"company": company, "title": page["title"],
                    "method": "exact title match", "candidates": [], "notes": ""}

    search = await client.get_json(API, params={
        "action": "query", "list": "search", "srsearch": brand,
        "srlimit": 5, "format": "json"})
    hits = [h["title"] for h in ((search.get("query") or {}).get("search") or [])]
    if not hits:
        return {"company": company, "title": None, "method": "no search result",
                "candidates": [], "notes": "no article found"}
    return {"company": company, "title": hits[0], "method": "search, top hit",
            "candidates": hits,
            "notes": "top hit taken; check against the candidate list"}


async def fetch_creation(client: RetryingClient, title: str) -> str | None:
    """First revision timestamp: when the article started existing."""
    payload = await client.get_json(API, params={
        "action": "query", "prop": "revisions", "titles": title,
        "rvdir": "newer", "rvlimit": 1, "rvprop": "timestamp", "format": "json"})
    pages = (payload.get("query") or {}).get("pages") or {}
    for page in pages.values():
        revs = page.get("revisions") or []
        if revs:
            return revs[0].get("timestamp")
    return None


async def fetch_redirects(client: RetryingClient, title: str,
                          limit: int = 8) -> list[str]:
    """Titles that redirect INTO this article.

    Needed because pageviews are keyed on the title string, not the article. When
    Wikipedia renames a page the history stays under the old title, so the new
    one looks newly-popular: "Zoom Communications" showed a median of 16 views
    against a 19,749 peak, because most of Zoom's traffic still sits under
    "Zoom Video Communications".

    Summing the article and its redirects also measures the thing this module
    actually wants -- total attention to the entity, however the reader
    navigated to it -- rather than attention to one spelling of its name.
    """
    payload = await client.get_json(API, params={
        "action": "query", "prop": "redirects", "titles": title,
        "rdlimit": limit, "format": "json"})
    pages = (payload.get("query") or {}).get("pages") or {}
    out: list[str] = []
    for page in pages.values():
        for r in page.get("redirects") or []:
            if r.get("title"):
                out.append(r["title"])
    return out[:limit]


async def fetch_pageviews(client: RetryingClient, title: str,
                          end: date) -> list[dict[str, Any]]:
    """Monthly views for the whole available range, in one request.

    The full range rather than the study window: it costs the same one call, and
    it means a DRS-anchored analysis still has data for the companies whose
    confidential filing predates the S-1 by years.
    """
    url = PAGEVIEWS.format(
        title=title.replace(" ", "_"),
        start=COVERAGE_START.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    try:
        payload = await client.get_json(url)
    except Exception as exc:
        # A 404 here means "no pageview data for this title", which is a fact
        # about the article, not a failure of the run.
        logger.warning("pageviews: %s -> %s", title, type(exc).__name__)
        return []
    return payload.get("items") or []


def _tier_a_sample(size: int, seed: int = 0) -> list[dict[str, str]]:
    """Stratified sample of priced Tier A issuers, by year x deal-size bucket.

    Stratified rather than random so the sample keeps the population's shape on
    the two dimensions the watchlist is most skewed on. Deterministic seed, so
    the cohort is reproducible and cannot be reshuffled after seeing a result.
    """
    import pandas as pd

    from research.collect.paths import CENSUS_PARQUET

    census = pd.read_parquet(CENSUS_PARQUET)
    census = census[(census["status"] == "priced") & census["name"].ne("")].copy()
    census["year"] = pd.to_datetime(census["calendar_date"]).dt.year
    census["deal_usd"] = pd.to_numeric(census["total_shares_value"], errors="coerce")
    census["bucket"] = pd.cut(census["deal_usd"], [0, 5e7, 2e8, 1e9, float("inf")],
                              labels=["<50M", "50-200M", "200M-1B", ">1B"])
    census = census.dropna(subset=["bucket"])

    frac = size / len(census)
    picked = (census.groupby(["year", "bucket"], observed=True, group_keys=False)
              .apply(lambda g: g.sample(max(1, round(len(g) * frac)),
                                        random_state=seed)
                     if len(g) else g))
    picked = picked.head(size)
    return [{"company": r["name"], "cohort": "tier_a_sample",
             "symbol": _text(r.get("symbol"))} for _, r in picked.iterrows()]


def _text(value: object) -> str:
    """Missing-safe string. `pd.NA or ""` raises: NA has no truth value."""
    import pandas as pd

    return "" if value is None or pd.isna(value) else str(value)


def _tier_b() -> list[dict[str, str]]:
    from research.collect.edgar_enrich import read_watchlist_df

    wl = read_watchlist_df()
    return [{"company": r["company"], "cohort": "tier_b",
             "symbol": _text(r.get("finnhub_symbol"))}
            for _, r in wl.iterrows()]


async def collect(cohort: str, *, sample_size: int = 150, refetch: bool = False,
                  include_redirects: bool = True) -> dict[str, int]:
    ensure_dirs()
    RAW_WIKI.mkdir(parents=True, exist_ok=True)
    s = get_research_settings()

    if cohort == "tier_b":
        targets = _tier_b()
    elif cohort == "tier_a_sample":
        targets = _tier_a_sample(sample_size)
    else:
        raise SystemExit(f"unknown cohort {cohort!r}")

    logger.info("wikipedia: %d targets in cohort %s", len(targets), cohort)
    today = datetime.now(UTC).date()

    # Wikimedia asks for a descriptive User-Agent with a contact address and
    # throttles anonymous clients that omit one. Reusing SEC_USER_AGENT, which
    # already carries a real address.
    client = RetryingClient(user_agent=s.sec_user_agent, per_second=4.0,
                            max_retries=3, base_backoff=3.0)
    stats = {"resolved": 0, "unresolved": 0, "with_views": 0, "cached": 0}
    resolutions: list[dict[str, Any]] = []
    try:
        for target in targets:
            company = target["company"]
            path = RAW_WIKI / f"{slug(company)}.json"
            if path.exists() and not refetch:
                stats["cached"] += 1
                resolutions.append(json.loads(path.read_text())["resolution"])
                continue

            res = await resolve_title(client, company)
            res["cohort"] = target["cohort"]
            title = res.get("title")
            created = await fetch_creation(client, title) if title else None
            items = await fetch_pageviews(client, title, today) if title else []

            # Add the redirects' traffic, keyed by month. A renamed article
            # leaves its history under the old title; without this the series is
            # silently truncated at the rename.
            per_title = {title: sum(i.get("views") or 0 for i in items)} if title else {}
            if title and include_redirects:
                merged: dict[str, int] = {i["timestamp"]: i.get("views") or 0
                                          for i in items}
                for alias in await fetch_redirects(client, title):
                    alias_items = await fetch_pageviews(client, alias, today)
                    if not alias_items:
                        continue
                    per_title[alias] = sum(i.get("views") or 0 for i in alias_items)
                    for i in alias_items:
                        merged[i["timestamp"]] = (merged.get(i["timestamp"], 0)
                                                  + (i.get("views") or 0))
                items = [{"timestamp": ts, "views": v}
                         for ts, v in sorted(merged.items())]

            if title:
                stats["resolved"] += 1
            else:
                stats["unresolved"] += 1
            if items:
                stats["with_views"] += 1

            # Pageviews are keyed on the TITLE STRING, so a title that used to
            # be a redirect or a different subject carries that traffic. "Figma"
            # returns views back to 2015 because `figma` is also a Max Factory
            # action-figure line; the company's article dates from 2020. Detected
            # by comparing the first pageview month against article creation, and
            # recorded so the aggregate can invalidate those months rather than
            # read someone else's traffic as pre-IPO attention.
            first_month = items[0]["timestamp"][:6] if items else None
            inherited = bool(
                created and first_month and first_month < created[:4] + created[5:7])
            path.write_text(json.dumps({
                "company": company,
                "cohort": target["cohort"],
                "symbol": target.get("symbol", ""),
                "resolution": res,
                "article_created": created,
                "coverage_start": COVERAGE_START.isoformat(),
                "first_pageview_month": first_month,
                # Views per contributing title, so the merge is auditable and a
                # redirect that dragged in unrelated traffic is findable.
                "views_by_title": per_title,
                "redirects_included": include_redirects,
                # True: months before `article_created` belong to a previous
                # occupant of this title and must not be treated as this
                # company's attention.
                "title_history_inherited": inherited,
                "fetched_at": datetime.now(UTC).isoformat(),
                "items": items,
            }, indent=1) + "\n")
            resolutions.append(res)
            logger.info("%-28s -> %-32s created=%s months=%d",
                        company[:28], str(title)[:32],
                        (created or "-")[:10], len(items))
    finally:
        await client.aclose()

    import csv

    if resolutions:
        fields = ["company", "cohort", "title", "method", "notes", "candidates"]
        with WIKI_RESOLUTION_CSV.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in resolutions:
                w.writerow({**r, "candidates": "|".join(r.get("candidates") or [])})
        logger.info("wikipedia: resolution log -> %s", WIKI_RESOLUTION_CSV)
    return stats


def _edgar_floor() -> dict[str, "object"]:
    """company -> earliest EDGAR trace, as an attention-validity floor.

    Article creation date is a necessary but insufficient integrity check,
    because a title can be **repurposed**: the "Bullish" article dates from 2005,
    when the title held the market term, and was rewritten for the 2021 crypto
    exchange. Creation date sees a 2005 article and waves through fifteen years
    of someone else's traffic.

    So each company also gets a floor from its own earliest filing -- Form D,
    DRS or S-1, whichever came first. A company whose first EDGAR trace is 2021
    did not accumulate Wikipedia attention in 2016, and pageviews claiming
    otherwise belong to whatever previously occupied the title.

    This is deliberately conservative. It can clip genuine early attention for a
    company that was famous long before it filed anything, so `views` is always
    retained and only `valid_attention` is gated -- the raw series stays
    inspectable. The asymmetry is on purpose: wrongly admitting another
    subject's traffic manufactures exactly the "attention rose before the
    filing" shape this module exists to test for, which is the more dangerous
    error.
    """
    import pandas as pd

    from research.collect.edgar_events import EVENTS_PARQUET

    if not EVENTS_PARQUET.exists():
        logger.warning("wikipedia: no edgar_events.parquet; validity floor will "
                       "rest on article creation alone")
        return {}
    ev = pd.read_parquet(EVENTS_PARQUET)
    floors: dict[str, object] = {}
    for _, row in ev.iterrows():
        # `if c` on a pandas <NA> raises: NA has no truth value. Guard with
        # pd.notna, not truthiness.
        candidates = [row.get(c) for c in ("form_d_first", "drs_first", "s1_first")]
        dates = [pd.to_datetime(c, errors="coerce")
                 for c in candidates if c is not None and pd.notna(c)]
        dates = [d for d in dates if pd.notna(d)]
        if dates:
            floors[row["company"]] = min(dates)
    return floors


def aggregate() -> int:
    """Monthly panel from cached payloads. No network."""
    import pandas as pd

    floors = _edgar_floor()
    records = []
    for path in sorted(RAW_WIKI.glob("*.json")):
        blob = json.loads(path.read_text())
        created_raw = blob.get("article_created")
        created = (pd.to_datetime(created_raw, errors="coerce")
                   if created_raw else pd.NaT)
        for item in blob.get("items") or []:
            month = pd.to_datetime(item["timestamp"][:6] + "01", format="%Y%m%d")
            records.append({
                "company": blob["company"],
                "cohort": blob.get("cohort"),
                "title": (blob.get("resolution") or {}).get("title"),
                "month": month.strftime("%Y-%m"),
                "views": int(item.get("views") or 0),
                "article_created": created,
                # True only when the article existed for the whole month. The
                # month of creation is partial and marked as such rather than
                # compared against full months.
                # False for any month before the article existed. Those months
                # are either genuinely absent or belong to a previous occupant of
                # the title; either way they are NOT this company's attention and
                # the analysis must exclude rather than zero-fill them.
                "article_existed": bool(pd.notna(created)
                                        and month >= created.tz_localize(None)
                                        .replace(day=1)),
                "title_history_inherited": bool(blob.get("title_history_inherited")),
                "edgar_floor": floors.get(blob["company"]),
                "creation_month": bool(pd.notna(created) and month.to_period("M")
                                       == created.tz_localize(None).to_period("M")),
            })
    if not records:
        logger.warning("wikipedia: nothing cached to aggregate")
        return 0
    df = pd.DataFrame.from_records(records).sort_values(["company", "month"])

    month_start = pd.to_datetime(df["month"] + "-01")
    floor = pd.to_datetime(df["edgar_floor"], errors="coerce")
    override = pd.to_datetime(
        df["company"].map(HAND_VERIFIED_FLOOR).astype("string") + "-01",
        errors="coerce")
    df["floor_source"] = override.notna().map(
        {True: "hand-verified", False: "article creation + earliest EDGAR filing"})
    effective = override.where(override.notna(), floor)

    # Valid once BOTH hold: the article existed, and the company itself has a
    # trace by then. Where no floor is known the article test stands alone, and
    # that is recorded rather than assumed away.
    df["valid_attention"] = df["article_existed"] & (
        effective.isna()
        | (month_start >= effective.dt.to_period("M").dt.to_timestamp()))
    # A hand-verified floor overrides the article-existence test too, since it is
    # a statement that the title was about this company from that month on.
    hand = override.notna()
    df.loc[hand, "valid_attention"] = (
        month_start[hand] >= override[hand].dt.to_period("M").dt.to_timestamp())
    df["views_valid"] = df["views"].where(df["valid_attention"])

    df.to_parquet(WIKI_PARQUET, index=False)
    n_valid = int(df["valid_attention"].sum())
    logger.info("wikipedia: %d company-months across %d companies "
                "(%d valid, %d excluded as pre-article or pre-EDGAR) -> %s",
                len(df), df["company"].nunique(), n_valid, len(df) - n_valid,
                WIKI_PARQUET)
    return len(df)


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", default="tier_b",
                    choices=("tier_b", "tier_a_sample"))
    ap.add_argument("--sample-size", type=int, default=150)
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--no-redirects", action="store_true",
                    help="count only the exact title; omits history left behind "
                         "by a page rename")
    ap.add_argument("--aggregate-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if not args.aggregate_only:
        stats = await collect(args.cohort, sample_size=args.sample_size,
                              refetch=args.refetch,
                              include_redirects=not args.no_redirects)
        print(f"wikipedia: resolved={stats['resolved']} "
              f"unresolved={stats['unresolved']} with_views={stats['with_views']} "
              f"cached={stats['cached']}")
    aggregate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
