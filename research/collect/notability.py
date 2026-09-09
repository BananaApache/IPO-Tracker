"""Do IPOs of companies notable enough to have a Wikipedia article get
underpriced more than IPOs of companies that do not?

A redesign of the attention/underpricing study in `collect/underpricing.py`,
which is capped at **n=35** because it needs pageview *levels* and so needs an
article to exist. Article **existence** is determinable for 100% of any sample,
which lifts the eligible population to **1,256** and the detectable effect from
|r| ~ 0.32 to ~0.09 at n=500.

The trade is a coarser construct. "Has a Wikipedia article" is a proxy for
*notability*, which correlates with size, sector and institutional demand -- and
underpricing is driven by demand. This can therefore support a **predictive**
claim ("notable companies' IPOs are underpriced more"), not a causal one, and it
is not a measurement of attention. Reported with and without controls so the
reader can see how much survives.

Three things would make the result an artifact rather than a finding. Each is
handled explicitly, and the first is fatal if missed.

**1. SPACs.** Measured on this census: **98% of SPAC-like listings price at
exactly $10.00** against 6% of operating companies, they have ~0% first-day pop
by construction, and they essentially never have a Wikipedia article. Pooling
them manufactures a strong "no article -> no pop" correlation that is entirely
the SPAC/operating distinction. There are 1,360 of them among 2,922 priced rows
-- more than half -- so this is not a robustness check, it is the difference
between a finding and an artifact.

**2. Endogenous article creation.** An article created *because* of the IPO is an
effect of the outcome, not pre-existing notability. Treatment therefore requires
the article to have been created before `listing_date - cutoff_days`. The census
has no S-1 dates, so the cutoff stands in for "before the public knew": measured
on the watchlist, the public S-1 precedes listing by a median of 27 days and a
90th percentile of 74, so the 90-day default clears the registration window for
roughly 93% of deals. `--cutoff-days` runs the sensitivity.

**3. Entity-resolution false positives.** For a binary treatment a false positive
*flips* the assignment, which is worse than the noise it adds to a continuous
regressor. Search-based resolution was measured returning confidently wrong
entities -- SharonAI -> OpenAI, BKV Corp -> Devon Energy, Legence -> VF
Corporation -- so this uses **exact-title matching only**, accepts the recall
loss, and requires the resolved title to share a distinctive token with the
registrant name. `--audit` prints a sample for hand-checking in both directions.

Run:  uv run --group research python -m research.collect.notability --sample 500
      uv run --group research python -m research.collect.notability --resolve
      uv run --group research python -m research.collect.notability --prices
      uv run --group research python -m research.collect.notability --build
"""

import argparse
import asyncio
import json
import logging
import pathlib
import re
import sys
from datetime import UTC, date, datetime, timedelta

from research.collect.paths import CENSUS_PARQUET, DATA, RAW, ensure_dirs

logger = logging.getLogger(__name__)

RAW_NOTABILITY = RAW / "notability"
SAMPLE_PARQUET = DATA / "notability_sample.parquet"
NOTABILITY_PARQUET = DATA / "notability.parquet"

# Eligibility, fixed before any outcome is observed.
MIN_OFFER_PRICE = 5.0
CUTOFF_DAYS = 90

# Two cohorts, because the study and the published result it is compared against
# share **zero tickers**: the paper covers 2006-2016 and the census as first
# built started in 2019. With no overlap it is impossible to tell whether a null
# here is an era effect or a broken pipeline, so `replication` reruns the same
# code on the paper's window. Any difference then isolates the method.
COHORTS: dict[str, tuple[int, int]] = {
    "recent": (2019, 2026),
    "replication": (2006, 2016),   # the published study's window
}


def cohort_paths(cohort: str) -> dict[str, pathlib.Path]:
    """Output paths for one cohort.

    The default cohort keeps the unsuffixed filenames the earlier run wrote, so
    adding a second cohort cannot invalidate the first.
    """
    if cohort not in COHORTS:
        raise SystemExit(f"unknown cohort {cohort!r}; choose from {sorted(COHORTS)}")
    sfx = "" if cohort == "recent" else f"_{cohort}"
    return {
        "sample": DATA / f"notability_sample{sfx}.parquet",
        "articles": RAW_NOTABILITY / f"articles{sfx}.json",
        "articles_ext": RAW_NOTABILITY / f"articles_extended{sfx}.json",
        "union": RAW_NOTABILITY / f"articles_union{sfx}.json",
        "prices": RAW_NOTABILITY / f"day1_prices{sfx}.json",
        "out": DATA / f"notability{sfx}.parquet",
    }

# Corporate suffixes stripped when forming candidate article titles. Wikipedia
# titles the company, not the registrant string: "Reddit, Inc." is at "Reddit".
SUFFIXES = re.compile(
    r"[,\.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|"
    r"holdings?|group|llc|lp|sa|nv|ag|ab|as|oyj|spa|se|class\s+[ab])\.?$",
    re.I)
STOPWORDS = {"the", "group", "holdings", "holding", "technologies", "technology",
             "international", "global", "systems", "solutions", "corp", "inc",
             "company", "co", "ltd", "limited", "plc", "and", "of"}


def is_spac(name: str, symbol: str, offer: float) -> tuple[bool, str]:
    """SPAC-like? Returns (verdict, which rule fired).

    Two high-precision rules. Ticker-suffix 'U' alone is NOT used as a rule --
    Unity trades as plain "U" -- so the unit pattern requires 4-5 characters
    ending in U, which is the actual unit-ticker convention (AACIU, FVNNU).
    """
    if re.search(r"acquisition", name or "", re.I):
        return True, "name contains 'acquisition'"
    if re.fullmatch(r"[A-Z]{3,4}U", (symbol or "").upper()):
        return True, "unit-ticker pattern"
    return False, ""


def eligible(cohort: str = "recent"):
    """The population, before sampling. No outcome variable is touched here."""
    import pandas as pd

    lo, hi = COHORTS[cohort]

    cen = pd.read_parquet(CENSUS_PARQUET)
    p = cen[(cen["status"] == "priced") & cen["symbol"].notna()
            & cen["price_low"].notna()
            & (cen["price_low"] == cen["price_high"])].copy()
    p["offer_price"] = p["price_low"].astype(float)

    flags = [is_spac(r["name"], r["symbol"], r["offer_price"])
             for _, r in p.iterrows()]
    p["is_spac"] = [f[0] for f in flags]
    p["spac_rule"] = [f[1] for f in flags]
    # Reported separately rather than folded into the SPAC rule: 6% of operating
    # companies genuinely price at $10.00, so excluding on price alone would drop
    # real listings. The analysis reports the result with and without it.
    p["offer_exactly_ten"] = p["offer_price"].eq(10.0)

    # A $10.00 offer is excluded as well. Measured on a 500-row draw: 48 rows
    # survived the name/ticker SPAC rules, and the set priced at exactly $10.00
    # is EXACTLY that same 48 -- apostrophe unit tickers (GIK'U, CCV'U), SPAC
    # sponsors (Churchill Capital Corp V, SVF Investment Corp 2), and operating
    # companies that listed via SPAC merger (Lightning eMotors, Hagerty). All
    # share the $10/no-pop structure that would manufacture the finding, and the
    # price rule catches every one of them.
    #
    # The cost is real: ~6% of genuine operating IPOs price at $10.00 and are
    # lost too. That is the right trade -- a diluted treatment group weakens a
    # true effect, whereas SPAC contamination invents one.
    keep = p[~p["is_spac"] & (p["offer_price"] >= MIN_OFFER_PRICE)
             & ~p["offer_exactly_ten"]].copy()
    keep["listing_date"] = pd.to_datetime(keep["calendar_date"])
    keep["year"] = keep["listing_date"].dt.year
    keep = keep[(keep["year"] >= lo) & (keep["year"] <= hi)].copy()
    keep["deal_usd"] = pd.to_numeric(keep["total_shares_value"], errors="coerce")
    keep["size_bucket"] = pd.cut(keep["deal_usd"],
                                 [0, 5e7, 2e8, 1e9, float("inf")],
                                 labels=["<50M", "50-200M", "200M-1B", ">1B"])
    logger.info("eligible[%s %d-%d]: %d of %d priced rows (%d SPAC-like, %d "
                "under $%.0f)", cohort, lo, hi, len(keep), len(p),
                int(p["is_spac"].sum()),
                int((~p["is_spac"] & (p["offer_price"] < MIN_OFFER_PRICE)).sum()),
                MIN_OFFER_PRICE)
    return keep


def build_sample(size: int, seed: int = 0, cohort: str = "recent"):
    """Stratified random draw by year x deal-size bucket.

    Stratified so the sample keeps the population's shape on the two dimensions
    underpricing is most known to vary on. Deterministic seed, written to disk
    before any price or article is fetched, so the cohort cannot be reshuffled
    after seeing a result.
    """
    import pandas as pd

    paths = cohort_paths(cohort)
    pop = eligible(cohort).dropna(subset=["size_bucket"])
    frac = min(1.0, size / len(pop))
    # groupby().sample() rather than groupby().apply(): apply() folds the
    # grouping columns into the index and drops them from the result, which is
    # how an earlier version of this lost `year` and `size_bucket`.
    picked = (pop.groupby(["year", "size_bucket"], observed=True)
              .sample(frac=frac, random_state=seed)
              .sample(frac=1.0, random_state=seed))
    if len(picked) > size:
        picked = picked.head(size)
    elif len(picked) < size:
        # Proportional rounding can come up short; top up from the remainder
        # rather than over-sampling any one stratum.
        rest = pop.drop(index=picked.index)
        picked = pd.concat([picked, rest.sample(min(size - len(picked), len(rest)),
                                                random_state=seed)])
    cols = ["name", "symbol", "listing_date", "offer_price", "shares", "deal_usd",
            "exchange", "year", "size_bucket", "offer_exactly_ten"]
    out = picked[cols].reset_index(drop=True)
    out.to_parquet(paths["sample"], index=False)
    logger.info("sample[%s]: %d rows, %s to %s -> %s", cohort, len(out),
                out["listing_date"].min().date(), out["listing_date"].max().date(),
                paths["sample"].name)
    return out


def candidate_titles(name: str) -> list[str]:
    """Exact-title candidates for a registrant name, most specific first."""
    base = re.sub(r"\s+", " ", (name or "").strip())
    out, seen = [], set()
    for cand in (base, SUFFIXES.sub("", base), SUFFIXES.sub("", SUFFIXES.sub("", base))):
        cand = cand.strip(" ,.")
        if cand and cand.casefold() not in seen:
            seen.add(cand.casefold())
            out.append(cand)
            if cand.isupper() and len(cand) > 4:
                out.append(cand.title())
    return out


# Wikidata P31 values that mean "this article is about an organisation".
# Needed because a shared token is not enough: "Andersen Group Inc." matched the
# article "Andersen", which is a Danish patronymic SURNAME page. The token guard
# cannot catch that -- the token genuinely matches -- so the entity's own type is
# checked instead.
COMPANY_TYPES = {
    "Q4830453",   # business
    "Q783794",    # company
    "Q891723",    # public company
    "Q6881511",   # enterprise
    "Q167037",    # corporation
    "Q43229",     # organization
    "Q1058914",   # software company
    "Q18388277",  # technology company
    "Q219577",    # holding company
    "Q210167",    # video game developer
    "Q507619",    # retail chain
    "Q4830453",
}
# Properties only an organisation carries. Used as a fallback when P31 is an
# unusual subtype not in the list above.
ORG_PROPERTIES = {"P452", "P1454", "P159", "P414"}  # industry, legal form, HQ, exchange


def shares_distinctive_token(name: str, title: str) -> bool:
    """Does the article title share a non-generic word with the company name?

    The guard against the failure this design cannot tolerate: `Legence Corp.`
    resolving to `VF Corporation` because both contain "Corporation".
    """
    def toks(s):
        return {w for w in re.findall(r"[a-z0-9]+", (s or "").casefold())
                if w not in STOPWORDS and len(w) > 2}
    a, b = toks(name), toks(title)
    return bool(a & b)


WIKI_API = "https://en.wikipedia.org/w/api.php"
ARTICLES_JSON = RAW_NOTABILITY / "articles.json"


async def resolve_articles(*, refetch: bool = False, cohort: str = "recent") -> dict:
    """Strict exact-title existence for every sampled company, plus creation date.

    Existence is checked in batches of 50 titles per request -- MediaWiki's
    `titles` parameter accepts up to 50 -- so ~1,300 companies with ~2.5
    candidate titles each costs on the order of 60 calls rather than 3,000.

    Creation dates cannot be batched (`rvlimit` is rejected alongside multiple
    titles), so they cost one call per *hit*, which is the small side.

    Disambiguation pages are treated as misses: "Medline" is a disambiguation
    page for the NIH database, not the medical-supply company.
    """
    import pandas as pd

    from backend.http import RetryingClient
    from research.collect.config import get_research_settings

    ensure_dirs()
    RAW_NOTABILITY.mkdir(parents=True, exist_ok=True)
    paths = cohort_paths(cohort)
    if paths["articles"].exists() and not refetch:
        logger.info("articles: using cached %s", paths["articles"].name)
        return json.loads(paths["articles"].read_text())

    sample = pd.read_parquet(paths["sample"])
    # title -> [company names that proposed it]
    proposals: dict[str, list[str]] = {}
    for _, row in sample.iterrows():
        for t in candidate_titles(row["name"]):
            proposals.setdefault(t, []).append(row["name"])

    titles = sorted(proposals)
    logger.info("articles: %d companies -> %d candidate titles, %d batches",
                len(sample), len(titles), -(-len(titles) // 50))

    existing: dict[str, dict] = {}
    # Wikimedia rejects requests without a descriptive User-Agent -- an earlier
    # version used a bare httpx client and got HTTP 403 on every batch after the
    # first two. RetryingClient supplies the agent and the backoff, and is the
    # same client collect/wikipedia.py uses.
    settings = get_research_settings()
    client = RetryingClient(user_agent=settings.sec_user_agent, per_second=4.0,
                            max_retries=3, base_backoff=3.0)
    batches_ok = batches_failed = 0
    try:
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            try:
                payload = await client.get_json(WIKI_API, params={
                    "action": "query", "titles": "|".join(batch), "redirects": 1,
                    "prop": "pageprops", "format": "json"})
            except Exception as exc:
                logger.warning("articles: batch %d failed: %s", i // 50,
                               type(exc).__name__)
                batches_failed += 1
                continue
            batches_ok += 1
            q = payload.get("query") or {}
            # `redirects` and `normalized` map the requested title to the final
            # one; both are needed to attribute a hit back to its proposer.
            alias = {}
            for kind in ("normalized", "redirects"):
                for m in q.get(kind) or []:
                    alias[m["from"]] = m["to"]
            for page in (q.get("pages") or {}).values():
                if "missing" in page:
                    continue
                title = page.get("title")
                props = page.get("pageprops") or {}
                existing[title] = {"title": title,
                                   "disambiguation": "disambiguation" in props,
                                   "qid": props.get("wikibase_item")}
            for src, dst in alias.items():
                if dst in existing:
                    existing.setdefault(src, dict(existing[dst], via=dst))

        # A mostly-failed sweep must NOT be cached. The cache is also the resume
        # mechanism, so writing a zero-hit result would make every later run
        # read "no company has an article" as a measurement. The twitter
        # collector learned this the same way.
        if batches_failed > batches_ok:
            raise SystemExit(
                f"articles: {batches_failed} of {batches_ok + batches_failed} "
                f"batches failed; refusing to cache a result that would read as "
                f"'no company has an article'.")

        # Attribute one article per company, strictly.
        out: dict[str, dict] = {}
        for _, row in sample.iterrows():
            name = row["name"]
            hit = None
            for cand in candidate_titles(name):
                info = existing.get(cand)
                if not info or info.get("disambiguation"):
                    continue
                final = info.get("via") or info["title"]
                if not shares_distinctive_token(name, final):
                    # The guard against Legence Corp. -> VF Corporation.
                    continue
                hit = final
                break
            info = existing.get(hit) if hit else None
            out[name] = {"company": name, "symbol": row["symbol"],
                         "title": hit, "has_article": hit is not None,
                         "qid": (info or {}).get("qid"),
                         "article_created": None}

        # Verify each matched article is actually about an ORGANISATION.
        #
        # The token guard is necessary but not sufficient: "Andersen Group Inc."
        # matched the article "Andersen", a Danish patronymic surname page, and
        # the shared token is genuine so no string rule can reject it. Wikidata's
        # own type claim can. A false positive here FLIPS the treatment
        # assignment, which is the one error this design cannot absorb.
        qids = sorted({v["qid"] for v in out.values()
                       if v.get("has_article") and v.get("qid")})
        org_ok: dict[str, bool] = {}
        types: dict[str, list[str]] = {}
        for i in range(0, len(qids), 50):
            batch = qids[i:i + 50]
            try:
                payload = await client.get_json(
                    "https://www.wikidata.org/w/api.php",
                    params={"action": "wbgetentities", "ids": "|".join(batch),
                            "props": "claims", "format": "json"})
            except Exception as exc:
                logger.warning("wikidata: batch %d failed: %s", i // 50,
                               type(exc).__name__)
                continue
            for qid, ent in (payload.get("entities") or {}).items():
                claims = ent.get("claims") or {}
                p31 = [c["mainsnak"]["datavalue"]["value"]["id"]
                       for c in claims.get("P31", [])
                       if c.get("mainsnak", {}).get("datavalue")]
                types[qid] = p31
                if bool(set(p31) & COMPANY_TYPES) or bool(set(claims) & ORG_PROPERTIES):
                    org_ok[qid] = "verified"
                elif p31:
                    # Has a type, and none of them is organisation-like. These
                    # are the confident rejections: Huize County, Certara (a
                    # Swiss municipality), Neutron (a quantum particle), Asana
                    # (the yoga posture), Compass (a navigational instrument).
                    org_ok[qid] = "wrong_subject"
                else:
                    org_ok[qid] = "unknown"
        counts = {v: sum(1 for x in org_ok.values() if x == v)
                  for v in ("verified", "wrong_subject", "unknown")}
        logger.info("wikidata: %s of %d entities", counts, len(qids))

        # Tiebreak the no-P31 cases on the article's own opening sentence.
        # "no P31" is uninformative -- it covers real companies with thin
        # metadata (Vital Farms, DoubleVerify) and wrong-subject pages alike --
        # so guessing either way would be wrong. The lead sentence distinguishes
        # them cheaply and auditably, and the verdict is stored per company.
        unknown_qids = [q for q, v in org_ok.items() if v == "unknown"]
        by_qid = {v["qid"]: v for v in out.values() if v.get("qid")}
        extracts: dict[str, str] = {}
        for qid in unknown_qids:
            rec = by_qid.get(qid)
            if not rec:
                continue
            try:
                payload = await client.get_json(WIKI_API, params={
                    "action": "query", "titles": rec["title"], "prop": "extracts",
                    "exintro": 1, "explaintext": 1, "format": "json"})
            except Exception:
                continue
            for page in ((payload.get("query") or {}).get("pages") or {}).values():
                extracts[qid] = (page.get("extract") or "")[:400]
        COMPANY_LEAD = re.compile(
            r"\b(compan(y|ies)|corporation|inc\.|incorporated|firm|business|"
            r"manufacturer|retailer|bank|insurer|start-?up|holdings?|brand|"
            r"headquarter|publicly traded|nasdaq|nyse)\b", re.I)
        for qid in unknown_qids:
            txt = extracts.get(qid, "")
            org_ok[qid] = "verified_by_lead" if COMPANY_LEAD.search(txt) else "wrong_subject"
        logger.info("wikidata: tiebreak resolved %d unknowns -> %d verified",
                    len(unknown_qids),
                    sum(1 for q in unknown_qids if org_ok[q] == "verified_by_lead"))

        for rec in out.values():
            if not rec["has_article"]:
                continue
            qid = rec.get("qid")
            state = org_ok.get(qid, "unknown") if qid else "unknown"
            verified = state in ("verified", "verified_by_lead")
            rec["wikidata_types"] = types.get(qid, [])
            rec["org_state"] = state
            rec["org_verified"] = verified
            if not verified:
                # Demoted to "no article": the page exists but is not about a
                # company, so it is not evidence of corporate notability.
                rec["rejected_title"] = rec["title"]
                rec["rejection"] = "article is not a Wikidata organisation"
                rec["title"] = None
                rec["has_article"] = False

        # Creation dates for the hits only.
        hits = [v for v in out.values() if v["has_article"]]
        logger.info("articles: %d of %d companies matched an article; fetching "
                    "creation dates", len(hits), len(out))
        for n, rec in enumerate(hits, 1):
            try:
                payload = await client.get_json(WIKI_API, params={
                    "action": "query", "prop": "revisions", "titles": rec["title"],
                    "rvdir": "newer", "rvlimit": 1, "rvprop": "timestamp",
                    "format": "json"})
            except Exception:
                continue
            for page in ((payload.get("query") or {}).get("pages") or {}).values():
                revs = page.get("revisions") or []
                if revs:
                    rec["article_created"] = revs[0].get("timestamp")
            if n % 50 == 0:
                logger.info("  creation dates: %d/%d", n, len(hits))
    finally:
        await client.aclose()

    result = {"resolved_at": datetime.now(UTC).isoformat(),
               "batches_ok": batches_ok, "batches_failed": batches_failed,
               "method": "exact-title only, disambiguation excluded, "
                         "distinctive-token guard",
               "companies": out}
    paths["articles"].write_text(json.dumps(result, indent=1) + "\n")
    logger.info("articles: -> %s", paths["articles"].name)
    return result


PRICES_JSON = RAW_NOTABILITY / "day1_prices.json"


def _is_rate_limited(exc: BaseException) -> bool:
    """Is this exception a 429, anywhere in its cause chain?

    `backend.http.RetryingClient` reports the URL and attempt count in the
    message and keeps the HTTP error as `__cause__`, so the status code has to
    be read from the chain rather than from `str(exc)`.
    """
    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 6:
        response = getattr(cur, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True
        if "429" in str(cur):
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


# Measured: Tiingo's free tier answers "You have run over your hourly request
# allocation" at roughly 50 requests/hour. Paced just under it, because backoff
# alone loses tickers -- three were dropped after exhausting retries against a
# limit that resets on the hour, not in seconds.
TIINGO_PER_HOUR = 45


async def collect_prices(*, batch: int = 0, per_hour: int = TIINGO_PER_HOUR,
                         cohort: str = "recent") -> dict:
    """Day-one open and close for each sampled ticker, from Tiingo.

    One request per ticker over a narrow window around the listing date, cached
    per ticker so a throttle or an interruption costs nothing on resume. Tiingo
    publishes no usage endpoint and returns no rate-limit headers, so `--batch`
    exists to spread the run if the free tier throttles.
    """
    import pandas as pd

    from backend.http import RetryingClient

    ensure_dirs()
    RAW_NOTABILITY.mkdir(parents=True, exist_ok=True)
    token = next(l.split("=", 1)[1].strip() for l in open(".env")
                 if l.startswith("TIINGOAPI_KEY"))

    paths = cohort_paths(cohort)
    sample = pd.read_parquet(paths["sample"])
    have = json.loads(paths["prices"].read_text()) if paths["prices"].exists() else {}
    todo = [r for _, r in sample.iterrows() if str(r["symbol"]) not in have]
    if batch:
        todo = todo[:batch]
    logger.info("prices: %d cached, %d to fetch", len(have), len(todo))

    # Quota is per CLOCK HOUR, so pacing is done against the hour boundary, not
    # with exponential backoff. An earlier version used RetryingClient's backoff
    # (120s, 240s, 480s, 900s) and stalled for 22 minutes on a single ticker
    # because the bucket does not refill gradually -- it refills at the top of
    # the hour. Retries are therefore disabled here and the loop sleeps to the
    # boundary itself.
    # Evenly spaced, not bursty: per_hour/3600 puts ~80 seconds between
    # requests, so 45/hour is delivered smoothly. An earlier version used
    # per_second=2.0 and fired all 45 in 22 seconds before idling 42 minutes --
    # which happened to work against a clock-hour bucket, but is exactly the
    # wrong shape if the window is rolling. Even spacing cannot trip either.
    client = RetryingClient(user_agent="IPOTracker-research/0.1",
                            per_second=per_hour / 3600.0, max_retries=1)

    async def wait_for_next_hour(why: str) -> None:
        now = datetime.now(UTC)
        nxt = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
        secs = max(5.0, (nxt - now).total_seconds())
        logger.info("prices: %s -- sleeping %.0f min until %s", why, secs / 60,
                    nxt.strftime("%H:%M UTC"))
        await asyncio.sleep(secs)

    used_this_hour = 0
    current_hour = datetime.now(UTC).hour
    logger.info("prices: %d to fetch at %d/hour -> ~%.1f hours",
                len(todo), per_hour, len(todo) / per_hour)
    fetched = failed = 0
    try:
        for row in todo:
            sym = str(row["symbol"])
            listing = pd.Timestamp(row["listing_date"]).date()

            hour_now = datetime.now(UTC).hour
            if hour_now != current_hour:
                current_hour, used_this_hour = hour_now, 0
            if used_this_hour >= per_hour:
                await wait_for_next_hour(f"{per_hour}/hour budget spent")
                current_hour, used_this_hour = datetime.now(UTC).hour, 0

            used_this_hour += 1
            try:
                bars = await client.get_json(
                    f"https://api.tiingo.com/tiingo/daily/{sym}/prices",
                    params={"startDate": (listing - timedelta(days=5)).isoformat(),
                            "endDate": (listing + timedelta(days=10)).isoformat(),
                            "token": token})
            except Exception as exc:
                # A 429 means the bucket is empty, not that the ticker is bad.
                # Sleep to the boundary and retry the SAME ticker rather than
                # burning it.
                #
                # The status code is NOT in the exception message: backend.http
                # raises HttpError("giving up on {url} after N attempts") and
                # attaches the httpx error as __cause__. An earlier version
                # tested `"429" in str(exc)` and so never recognised a 429,
                # which burned tickers one per rate-limited request.
                if _is_rate_limited(exc):
                    paths["prices"].write_text(json.dumps(have, indent=1) + "\n")
                    await wait_for_next_hour("hit 429")
                    current_hour, used_this_hour = datetime.now(UTC).hour, 1
                    try:
                        bars = await client.get_json(
                            f"https://api.tiingo.com/tiingo/daily/{sym}/prices",
                            params={"startDate": (listing - timedelta(days=5)).isoformat(),
                                    "endDate": (listing + timedelta(days=10)).isoformat(),
                                    "token": token})
                    except Exception:
                        logger.warning("prices: %s failed after an hour wait", sym)
                        failed += 1
                        continue
                else:
                    logger.warning("prices: %s failed: %s", sym, type(exc).__name__)
                    failed += 1
                    continue
            if not isinstance(bars, list) or not bars:
                # An empty response is diagnosed, not silently dropped. For an
                # older cohort the common cause is a TICKER RENAME: `FB` on
                # 2012-05-18 returns [] because Facebook is now META and Tiingo
                # is keyed on the current holder of the symbol. Left unmeasured
                # this quietly shrinks the sample toward surviving,
                # never-renamed firms -- a survivorship bias that would bite
                # hardest in exactly the replication window.
                note = "no bars in the requested window"
                meta = None
                try:
                    meta = await client.get_json(
                        f"https://api.tiingo.com/tiingo/daily/{sym}",
                        params={"token": token})
                except Exception:
                    pass
                if isinstance(meta, dict) and meta.get("startDate"):
                    if str(meta["startDate"])[:10] > listing.isoformat():
                        note = (f"ticker now held by an entity trading only from "
                                f"{str(meta['startDate'])[:10]} "
                                f"({meta.get('name')}) -- rename or symbol reuse")
                    else:
                        note = (f"ticker exists from {str(meta['startDate'])[:10]} "
                                f"but has no bars at the listing date")
                have[sym] = {"symbol": sym, "bars": [], "note": note,
                             "tiingo_name": (meta or {}).get("name"),
                             "tiingo_start": (meta or {}).get("startDate"),
                             "calendar_listing_date": listing.isoformat()}
                fetched += 1
                # Flushed here too. This branch counts as a fetch and costs up
                # to two calls, and on the 2006-2016 cohort it is the COMMON
                # case, not the rare one -- ticker reuse and renames are
                # everywhere. Falling through to the flush below only on a
                # successful fetch meant a long run of reused tickers persisted
                # nothing until the next hit, so an interrupted run re-spent an
                # hour of quota rediagnosing symbols it had already diagnosed.
                paths["prices"].write_text(json.dumps(have, indent=1) + "\n")
                logger.info("%-24s %s: %s", row["name"][:24], sym, note[:60])
                continue
            bars = sorted(bars, key=lambda b: b["date"])
            first = bars[0]
            have[sym] = {"symbol": sym, "first_trade_date": first["date"][:10],
                         "d1_open": first.get("open"), "d1_close": first.get("close"),
                         "d1_volume": first.get("volume"),
                         "calendar_listing_date": listing.isoformat(),
                         "n_bars": len(bars)}
            fetched += 1
            # Flushed after EVERY fetch, not every 50. This is a ten-hour
            # unattended run against a paid-quota API; losing up to 49 results
            # to an interruption also wastes an hour of allocation re-fetching
            # them. The file is small and the write is far cheaper than the
            # 80-second gap between requests.
            paths["prices"].write_text(json.dumps(have, indent=1) + "\n")
            if fetched % 25 == 0:
                logger.info("  prices: %d fetched, %d cached total", fetched,
                            len(have))
    finally:
        await client.aclose()
        paths["prices"].write_text(json.dumps(have, indent=1) + "\n")
    logger.info("prices[%s]: %d fetched, %d failed, %d total cached -> %s",
                cohort, fetched, failed, len(have), paths["prices"].name)
    return have


def _mann_whitney(a, b) -> dict:
    """Two-sided Mann-Whitney U with a normal approximation and tie correction.

    Hand-rolled because scipy is not a dependency of this project and adding one
    for a single rank test is not worth it. The normal approximation is standard
    at these group sizes (both well over 20) and the tie correction matters
    because underpricing has repeated values at exactly 0.
    """
    import numpy as np

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n1, n2 = len(a), len(b)
    # One shape on every return path. An earlier version omitted
    # `rank_biserial` when the variance was zero, so a caller reading the effect
    # size raised KeyError on all-tied input rather than getting a number.
    blank = {"n1": n1, "n2": n2, "u": None, "z": None, "p": None,
             "rank_biserial": None}
    if n1 < 3 or n2 < 3:
        return blank

    joint = np.concatenate([a, b])
    order = joint.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(joint) + 1)
    # Average ranks within tied groups.
    _, inv, counts = np.unique(joint, return_inverse=True, return_counts=True)
    for idx in np.flatnonzero(counts > 1):
        mask = inv == idx
        ranks[mask] = ranks[mask].mean()

    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    tie_term = sum(c ** 3 - c for c in counts if c > 1)
    n = n1 + n2
    var = (n1 * n2 / 12) * ((n + 1) - tie_term / (n * (n - 1)))
    rb = 2 * u1 / (n1 * n2) - 1
    if var <= 0:
        # Every value tied: the effect size is still defined (and zero), but no
        # z or p exists because there is no variance to standardise against.
        return {**blank, "u": float(u1), "rank_biserial": float(rb)}
    z = (u1 - mu) / var ** 0.5
    from math import erf, sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    # Rank-biserial correlation: the effect size that belongs with this test.
    return {"n1": n1, "n2": n2, "u": float(u1), "z": float(z),
            "p": float(min(p, 1.0)), "rank_biserial": float(rb)}


def build(*, cutoff_days: int = CUTOFF_DAYS, cohort: str = "recent"):
    """Join sample + articles + prices into the analysis table.

    Treatment is `has_article AND article_created <= listing_date -
    cutoff_days`. The cutoff stands in for "before the public knew": the census
    carries no S-1 dates, and measured on the watchlist the public S-1 precedes
    listing by a median of 27 days (90th percentile 74), so 90 days clears the
    registration window for roughly 93% of deals. An article created inside that
    window is plausibly an effect of the IPO rather than prior notability.
    """
    import pandas as pd

    paths = cohort_paths(cohort)
    sample = pd.read_parquet(paths["sample"])
    # The union is the treatment set: the two resolvers are complementary, not
    # nested, and either alone discards firms the other finds.
    arts_path = paths["union"] if paths["union"].exists() else paths["articles"]
    arts = (json.loads(arts_path.read_text())["companies"]
            if arts_path.exists() else {})
    logger.info("notability[%s]: treatment from %s", cohort, arts_path.name)
    prices = (json.loads(paths["prices"].read_text())
              if paths["prices"].exists() else {})

    rows = []
    for _, r in sample.iterrows():
        art = arts.get(r["name"], {})
        px = prices.get(str(r["symbol"]), {})
        created = pd.to_datetime(art.get("article_created"), errors="coerce", utc=True)
        listing = pd.Timestamp(r["listing_date"]).tz_localize("UTC")
        cutoff = listing - pd.Timedelta(days=cutoff_days)
        has = bool(art.get("has_article"))
        pre_existing = bool(has and pd.notna(created) and created <= cutoff)
        d1_close, offer = px.get("d1_close"), float(r["offer_price"])
        rows.append({
            "company": r["name"], "symbol": r["symbol"],
            "listing_date": r["listing_date"], "year": r["year"],
            "size_bucket": r["size_bucket"], "offer_price": offer,
            "deal_usd": r["deal_usd"],
            # Carried through for the regression's exchange fixed effects.
            "exchange": r.get("exchange"),
            "d1_open": px.get("d1_open"), "d1_close": d1_close,
            "first_trade_date": px.get("first_trade_date"),
            "underpricing": (d1_close / offer - 1) if d1_close else None,
            "opening_pop": (px["d1_open"] / offer - 1) if px.get("d1_open") else None,
            "has_article": has,
            "article_title": art.get("title"),
            "article_created": art.get("article_created"),
            "org_state": art.get("org_state"),
            # The treatment variable. `has_article` alone would count an article
            # created BY the IPO as prior notability.
            "notable_pre_ipo": pre_existing,
            "article_after_cutoff": bool(has and not pre_existing),
        })
    df = pd.DataFrame(rows)
    df.to_parquet(paths["out"], index=False)
    logger.info("notability[%s]: %d rows (%d with a price, %d notable pre-IPO) -> %s",
                cohort, len(df), int(df["underpricing"].notna().sum()),
                int(df["notable_pre_ipo"].sum()), paths["out"].name)
    return df


def analyse(*, cutoff_days: int = CUTOFF_DAYS, cohort: str = "recent") -> None:
    """Group comparison, then the same comparison inside strata."""
    import numpy as np
    import pandas as pd

    df = build(cutoff_days=cutoff_days, cohort=cohort)
    d = df.dropna(subset=["underpricing"]).copy()
    print(f"=== sample: {len(d)} of {len(df)} have a day-1 price "
          f"(collection is paced at 45/hour) ===\n")

    treat = d[d["notable_pre_ipo"]]["underpricing"] * 100
    ctrl = d[~d["notable_pre_ipo"]]["underpricing"] * 100
    print(f"{'group':28}{'n':>5}{'median %':>11}{'mean %':>10}{'IQR %':>18}")
    for label, g in (("notable pre-IPO (article)", treat), ("no prior article", ctrl)):
        if len(g):
            print(f"{label:28}{len(g):>5}{g.median():>11.1f}{g.mean():>10.1f}"
                  f"{f'{g.quantile(.25):.1f} to {g.quantile(.75):.1f}':>18}")

    res = _mann_whitney(treat, ctrl)
    if res.get("p") is not None:
        print(f"\nMann-Whitney U: z={res['z']:+.3f}  p={res['p']:.4f}  "
              f"rank-biserial={res['rank_biserial']:+.3f}")
        print(f"median difference: {treat.median() - ctrl.median():+.1f} "
              f"percentage points")
        print("  significant" if res["p"] < 0.05 else "  NOT significant at 0.05")
    else:
        print(f"\ntoo few in one group to test (n1={res['n1']}, n2={res['n2']})")

    # The confound the design most needs to answer: is this just deal size?
    print("\n=== inside deal-size strata (is it just size?) ===")
    print(f"{'size bucket':12}{'n treat':>9}{'n ctrl':>8}{'med treat':>11}"
          f"{'med ctrl':>10}{'diff pp':>9}")
    for b in ["<50M", "50-200M", "200M-1B", ">1B"]:
        s = d[d["size_bucket"] == b]
        t = s[s["notable_pre_ipo"]]["underpricing"] * 100
        c = s[~s["notable_pre_ipo"]]["underpricing"] * 100
        if len(t) and len(c):
            print(f"{b:12}{len(t):>9}{len(c):>8}{t.median():>11.1f}"
                  f"{c.median():>10.1f}{t.median() - c.median():>+9.1f}")
        else:
            print(f"{b:12}{len(t):>9}{len(c):>8}{'--':>11}{'--':>10}{'--':>9}")
    print("\nA difference that survives inside every size bucket is not deal size.")


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, metavar="N",
                    help="draw a stratified sample of N and freeze it to disk")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resolve", action="store_true",
                    help="strict article existence + Wikidata org verification")
    ap.add_argument("--resolve-extended", action="store_true",
                    help="Wikidata search for rule (1); queues rules (2)-(6)")
    ap.add_argument("--resolve-products", action="store_true",
                    help="rule (6): probe redirects out of each untreated firm's "
                         "own name for a product/service article (review only)")
    ap.add_argument("--merge", action="store_true",
                    help="union the resolvers and apply reviewed relation verdicts")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--prices", action="store_true",
                    help="day-1 prices from Tiingo, paced to the hourly allocation")
    ap.add_argument("--batch", type=int, default=0,
                    help="stop after this many price requests")
    ap.add_argument("--per-hour", type=int, default=TIINGO_PER_HOUR)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--analyse", action="store_true")
    ap.add_argument("--regress", action="store_true",
                    help="the pre-registered OLS: treatment plus controls, "
                         "year-quarter clustered SEs")
    ap.add_argument("--no-winsorize", action="store_true",
                    help="report the raw sample instead of 1/99 winsorised")
    ap.add_argument("--cutoff-days", type=int, default=CUTOFF_DAYS,
                    help="an article must predate listing minus this many days")
    ap.add_argument("--cohort", default="recent", choices=sorted(COHORTS),
                    help="'recent' is 2019-2026; 'replication' is the published "
                         "study's 2006-2016 window")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    ck = {"cohort": args.cohort}
    if args.sample:
        build_sample(args.sample, seed=args.seed, **ck)
    if args.resolve:
        await resolve_articles(refetch=args.refetch, **ck)
    if args.resolve_extended:
        await resolve_articles_extended(refetch=args.refetch, **ck)
    if args.resolve_products:
        await resolve_articles_products(refetch=args.refetch,
                                        cutoff_days=args.cutoff_days, **ck)
    if args.merge:
        merge_resolvers(args.cohort)
    if args.prices:
        await collect_prices(batch=args.batch, per_hour=args.per_hour, **ck)
    if args.analyse:
        analyse(cutoff_days=args.cutoff_days, **ck)
    if args.regress:
        regress(cohort=args.cohort, winsorize=not args.no_winsorize,
                cutoff_days=args.cutoff_days)
    if args.build and not (args.analyse or args.regress):
        build(cutoff_days=args.cutoff_days, **ck)
    if not any((args.sample, args.resolve, args.resolve_extended, args.merge,
                args.resolve_products, args.prices, args.build, args.analyse,
                args.regress)):
        ap.print_help()
    return 0




WD_API = "https://www.wikidata.org/w/api.php"
ARTICLES_EXT_JSON = RAW_NOTABILITY / "articles_extended.json"

# The published study's Exhibit A counts an article titled with the firm, its
# parent, a major subsidiary, a predecessor, a company it separated from, or its
# core product or service. These are the Wikidata properties that express those
# relations, checked outward from the company's own entity.
RELATION_RULES = [
    ("P749", "parent organisation"),          # rule (2)
    ("P127", "owned by"),                     # rule (2), the other direction
    ("P355", "has subsidiary"),               # rule (3)
    ("P1365", "replaces"),                    # rule (5), predecessor
    ("P1366", "replaced by"),                 # rule (5)
    ("P807", "separated from"),               # rule (4)
    ("P1056", "product or material produced"),  # rule (6)
    ("P1830", "owner of"),                    # rule (3), the other direction
]
# The paper sets the indicator to zero for an article with fewer than 30 words
# in its main body -- a stub or a redirect is not investor awareness.
MIN_ARTICLE_WORDS = 30


def name_variants(name: str) -> list[str]:
    """Search strings for one registrant name, most specific first.

    Wikidata's entity search is not tolerant of corporate suffixes: "Hertz
    Global Holdings Inc." returns nothing while "Hertz Global Holdings" resolves,
    and "NYMEX Holdings Inc." returns nothing while "NYMEX" reaches New York
    Mercantile Exchange through an alias. So the suffix-stripped and
    first-token forms are searched too.
    """
    base = re.sub(r"\s+", " ", (name or "").strip())
    out, seen = [], set()
    stripped = SUFFIXES.sub("", base).strip(" ,.")
    twice = SUFFIXES.sub("", stripped).strip(" ,.")
    head = " ".join(w for w in twice.split()[:2])
    for cand in (base, stripped, twice, head):
        c = cand.strip(" ,.")
        if c and len(c) > 2 and c.casefold() not in seen:
            seen.add(c.casefold())
            out.append(c)
    return out


def name_core(name: str) -> str:
    """Normalised comparison key for a company name.

    Token overlap is not a strong enough identity test for this design. Searching
    "Compass, Inc." surfaced an Italian bank subsidiary whose label shares the
    word "compass" and which is genuinely an organisation, so both earlier guards
    passed and the resolver followed its parent link to Banca Monte dei Paschi di
    Siena. Requiring the normalised cores to be EQUAL rejects that: "compass"
    against "compass banca".

    Suffixes are stripped repeatedly because registrant names stack them
    ("... Holdings Inc."), and punctuation goes so that "Couchbase, Inc." and
    "Couchbase Inc" agree.
    """
    core = re.sub(r"\s+", " ", (name or "").strip())
    for _ in range(3):
        core = SUFFIXES.sub("", core).strip(" ,.")
    core = re.sub(r"[^a-z0-9 ]", "", core.casefold()).strip()
    return re.sub(r"\s+", " ", core)


def _entity_is_org(claims: dict) -> bool:
    p31 = [c["mainsnak"]["datavalue"]["value"]["id"]
           for c in claims.get("P31", [])
           if c.get("mainsnak", {}).get("datavalue")]
    return bool(set(p31) & COMPANY_TYPES) or bool(set(claims) & ORG_PROPERTIES)


def _labels_and_aliases(ent: dict) -> list[str]:
    out = []
    lab = ((ent.get("labels") or {}).get("en") or {}).get("value")
    if lab:
        out.append(lab)
    out += [a.get("value") for a in ((ent.get("aliases") or {}).get("en") or [])
            if a.get("value")]
    return out


def _claim_targets(claims: dict, prop: str) -> list[str]:
    out = []
    for c in claims.get(prop, []):
        val = (c.get("mainsnak") or {}).get("datavalue") or {}
        qid = (val.get("value") or {}).get("id") if isinstance(val.get("value"), dict) else None
        if qid:
            out.append(qid)
    return out


async def resolve_articles_extended(*, refetch: bool = False,
                                   cohort: str = "recent") -> dict:
    """Article resolution following the published study's six matching rules.

    The strict resolver in `resolve_articles` attempts only exact titles built
    from the firm's own name, and it is too narrow in two separate ways:

      * **It misses the firm's own article.** GitLab and Couchbase both have
        company articles -- "GitLab Inc." and "Couchbase, Inc." -- that
        exact-title matching missed on capitalisation and a stripped period,
        landing on the software articles instead and rejecting them.
      * **It never attempts the other five rules.** Parent, subsidiary,
        predecessor, separated-from and core product are simply not tried, so
        Hertz Global Holdings -> "The Hertz Corporation" and NYMEX Holdings ->
        "New York Mercantile Exchange" cannot be found.

    Both push a binary treatment toward zero. This resolver goes through
    Wikidata instead: search the company by name, verify the entity really is
    that company, then take its English Wikipedia sitelink; if it has none,
    follow the relationship properties above to a related organisation that
    does.

    Two guards, because search is the dangerous step. The searched entity must
    itself be an organisation -- "Hertz" otherwise resolves to the SI unit of
    frequency -- and its label or an alias must share a distinctive token with
    the registrant name. The final article must also carry at least
    MIN_ARTICLE_WORDS words and not be a disambiguation page, matching the
    paper's own exclusions.
    """
    import pandas as pd

    from backend.http import RetryingClient
    from research.collect.config import get_research_settings

    ensure_dirs()
    RAW_NOTABILITY.mkdir(parents=True, exist_ok=True)
    paths = cohort_paths(cohort)
    if paths["articles_ext"].exists() and not refetch:
        logger.info("articles_ext: using cached %s", paths["articles_ext"].name)
        return json.loads(paths["articles_ext"].read_text())

    sample = pd.read_parquet(paths["sample"])
    settings = get_research_settings()
    client = RetryingClient(user_agent=settings.sec_user_agent, per_second=4.0,
                            max_retries=3, base_backoff=3.0)

    ent_cache: dict[str, dict] = {}

    async def get_entities(qids: list[str]) -> dict:
        want = [q for q in qids if q and q not in ent_cache]
        for i in range(0, len(want), 50):
            batch = want[i:i + 50]
            try:
                payload = await client.get_json(WD_API, params={
                    "action": "wbgetentities", "ids": "|".join(batch),
                    "props": "claims|sitelinks|labels|aliases",
                    "sitefilter": "enwiki", "languages": "en", "format": "json"})
            except Exception as exc:
                logger.warning("wikidata entities failed: %s", type(exc).__name__)
                continue
            for qid, ent in (payload.get("entities") or {}).items():
                ent_cache[qid] = ent
        return {q: ent_cache.get(q, {}) for q in qids if q}

    async def search(term: str) -> list[str]:
        try:
            payload = await client.get_json(WD_API, params={
                "action": "wbsearchentities", "search": term, "language": "en",
                "type": "item", "limit": 5, "format": "json"})
        except Exception:
            return []
        return [h["id"] for h in payload.get("search", []) if h.get("id")]

    def sitelink(ent: dict) -> str | None:
        return ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")

    out: dict[str, dict] = {}
    try:
        for n, (_, row) in enumerate(sample.iterrows(), 1):
            name = row["name"]
            rec = {"company": name, "symbol": row["symbol"], "title": None,
                   "has_article": False, "rule": None, "via_qid": None,
                   "evidence": None, "candidates_tried": []}

            for term in name_variants(name):
                qids = await search(term)
                rec["candidates_tried"].append({"term": term, "qids": qids})
                ents = await get_entities(qids)
                for qid in qids:
                    ent = ents.get(qid) or {}
                    claims = ent.get("claims") or {}
                    names = _labels_and_aliases(ent)
                    # Guard 1: the searched entity must BE this company, on
                    # normalised-name EQUALITY rather than token overlap. Token
                    # overlap let "Compass, Inc." match an Italian bank
                    # subsidiary ("Compass Banca") and follow its parent link to
                    # Banca Monte dei Paschi di Siena.
                    want = name_core(name)
                    if not any(name_core(t) == want for t in names if t):
                        continue
                    # Guard 2: and it must be an organisation. Without this,
                    # "Hertz" matches the SI unit of frequency.
                    if not _entity_is_org(claims):
                        continue

                    direct = sitelink(ent)
                    if direct:
                        rec.update(title=direct, has_article=True, rule="(1) the firm",
                                   via_qid=qid, evidence=f"enwiki sitelink on {qid}")
                        break

                    # Rules (2)-(6) are collected for REVIEW, never auto-accepted.
                    #
                    # They cannot be validated automatically, and the paper does
                    # not try: it hand-checks every assignment. The evidence that
                    # no name heuristic can work is in the paper's own examples --
                    # NYMEX Holdings -> "New York Mercantile Exchange" and
                    # Intersections Inc. -> "Identity Guard" share no token with
                    # the registrant name, so any name test that admits them also
                    # admits nonsense. Concretely: a Wikidata entity carries the
                    # alias "Compass", is genuinely an organisation, and passes
                    # normalised-name equality against "Compass, Inc." -- it is an
                    # Italian bank subsidiary, and following its parent link
                    # produced "Banca Monte dei Paschi di Siena".
                    #
                    # Auto-accepting that would put a false positive in the
                    # treatment group, which FLIPS an observation rather than
                    # adding noise. So these are queued and the count is
                    # reported; a reviewed set can be supplied later.
                    for prop, label in RELATION_RULES:
                        targets = _claim_targets(claims, prop)
                        if not targets:
                            continue
                        rel_ents = await get_entities(targets)
                        for tq in targets:
                            t_title = sitelink(rel_ents.get(tq) or {})
                            if t_title:
                                rec.setdefault("review_candidates", []).append({
                                    "rule": f"({prop}) {label}", "title": t_title,
                                    "from_qid": qid, "to_qid": tq})
                if rec["has_article"]:
                    break
            out[name] = rec
            if n % 50 == 0:
                logger.info("  articles_ext: %d/%d resolved, %d with an article",
                            n, len(sample),
                            sum(1 for v in out.values() if v["has_article"]))

        # Paper exclusions on the final article: no disambiguation pages, and at
        # least MIN_ARTICLE_WORDS words in the lead.
        titles = sorted({v["title"] for v in out.values() if v["title"]})
        logger.info("articles_ext: checking length and disambiguation on %d titles",
                    len(titles))
        meta: dict[str, dict] = {}
        for i in range(0, len(titles), 20):
            batch = titles[i:i + 20]
            try:
                payload = await client.get_json(WIKI_API, params={
                    "action": "query", "titles": "|".join(batch),
                    "prop": "extracts|pageprops", "exintro": 1, "explaintext": 1,
                    "format": "json"})
            except Exception:
                continue
            for page in ((payload.get("query") or {}).get("pages") or {}).values():
                extract = page.get("extract") or ""
                meta[page.get("title")] = {
                    "words": len(extract.split()),
                    "disambiguation": "disambiguation" in (page.get("pageprops") or {}),
                }
        for rec in out.values():
            if not rec["has_article"]:
                continue
            m = meta.get(rec["title"], {})
            rec["article_words"] = m.get("words")
            if m.get("disambiguation"):
                rec.update(has_article=False, rejection="disambiguation page",
                           rejected_title=rec["title"], title=None)
            elif (m.get("words") or 0) < MIN_ARTICLE_WORDS:
                rec.update(has_article=False,
                           rejection=f"lead under {MIN_ARTICLE_WORDS} words",
                           rejected_title=rec["title"], title=None)

        # Creation dates, for the endogeneity cutoff.
        hits = [v for v in out.values() if v["has_article"]]
        logger.info("articles_ext: %d with an article; fetching creation dates",
                    len(hits))
        for k, rec in enumerate(hits, 1):
            try:
                payload = await client.get_json(WIKI_API, params={
                    "action": "query", "prop": "revisions", "titles": rec["title"],
                    "rvdir": "newer", "rvlimit": 1, "rvprop": "timestamp",
                    "format": "json"})
            except Exception:
                continue
            for page in ((payload.get("query") or {}).get("pages") or {}).values():
                revs = page.get("revisions") or []
                if revs:
                    rec["article_created"] = revs[0].get("timestamp")
            if k % 50 == 0:
                logger.info("  creation dates: %d/%d", k, len(hits))
    finally:
        await client.aclose()

    for rec in out.values():
        rec.setdefault("article_created", None)
        rec.setdefault("review_candidates", [])
    n_review = sum(1 for v in out.values()
                   if not v["has_article"] and v["review_candidates"])
    logger.info("articles_ext: %d companies have rule (2)-(6) candidates awaiting "
                "manual review", n_review)
    result = {"resolved_at": datetime.now(UTC).isoformat(),
              "method": "Wikidata entity search for rule (1) -- the firm's own "
                        "article -- accepted automatically on normalised-name "
                        "equality plus an organisation check, with a >=30-word "
                        "lead and no disambiguation pages. Rules (2)-(6) "
                        "(parent, subsidiary, predecessor, separated-from, "
                        "product) are COLLECTED FOR REVIEW and never "
                        "auto-accepted: the published study hand-checks them, "
                        "and its own examples (NYMEX -> New York Mercantile "
                        "Exchange) share no name token with the registrant, so "
                        "no automatic name test can validate them.",
              "companies": out}
    paths["articles_ext"].write_text(json.dumps(result, indent=1) + "\n")
    logger.info("articles_ext: -> %s", paths["articles_ext"].name)
    return result


# Hand-verified verdicts on the rule (2)-(6) candidates, which is what the
# published study does for every assignment. Nine were queued; five would have
# been false positives had they been auto-accepted, so a 44% precision confirms
# these cannot be taken on trust.
REVIEWED_RELATIONS: dict[str, tuple[str | None, str]] = {
    # Accepted -- the relation is real and is the paper's rule (2) or (3).
    "WEBTOON Entertainment Inc.": ("Naver Corporation",
                                   "accept: Naver is WEBTOON's parent, rule (2)"),
    "KURA SUSHI USA, INC.": ("Kura Sushi",
                             "accept: Kura Sushi is the Japanese parent, rule (2)"),
    "PINTEREST, INC.": ("Pinterest",
                        "accept: this is the firm's own article, reached via the "
                        "brand entity, rule (1)"),
    "Snap One Holdings Corp.": ("Control4",
                                "accept: Control4 is a Snap One subsidiary, rule (3)"),
    # Rejected -- name coincidence or a generic concept, not the firm.
    "Compass, Inc.": (None, "reject: an Italian bank subsidiary aliased 'Compass'; "
                            "Banca Monte dei Paschi di Siena is unrelated"),
    "Avidity Biosciences, Inc.": (None, "reject: Novartis is not Avidity's parent"),
    "SONIM TECHNOLOGIES INC": (None, "reject: 'Mobile phone' is a generic concept, "
                                     "not the firm's core product article"),
    "Cyngn Inc.": (None, "reject: CyanogenMod is an unrelated project; name "
                         "coincidence only"),
    "JFrog Ltd": (None, "reject: 'DevOps' is a generic concept, not a product article"),
}

# Hand verdicts on the rule (6) redirect candidates, recorded BEFORE any
# replication-cohort price existed -- `day1_prices_replication.json` held 3 of
# 400 when these were written -- so the treatment revision could not have been
# steered by the outcome. Reviewed against the paper's Exhibit A: the target must
# be the firm, its renamed self, or its core product, and must predate listing by
# the cutoff.
#
# 3 accepted of 20 candidates. The 17 rejections are why this is reviewed rather
# than merged automatically: a redirect is strong evidence about *routing*, not
# about subject.
REVIEWED_PRODUCTS: dict[str, tuple[str | None, str]] = {
    # --- recent cohort (2019-2026) ---
    #
    # DISCLOSURE ON ORDERING: unlike the replication verdicts above, these were
    # reviewed when the recent cohort already had 456 of 500 prices, so the
    # outcome was visible. They apply the criteria frozen on the blind cohort --
    # target must be the firm, its renamed self, or its core product, and must
    # predate listing by the cutoff -- and every candidate is listed with its
    # verdict, accepted or not, so the selection is auditable rather than
    # asserted. Treat the replication cohort as the clean test.
    "SPACE EXPLORATION TECHNOLOGIES CORP": (
        "SpaceX",
        "accept: the firm's own article -- 'Space Exploration Technologies "
        "Corp., doing business as SpaceX'. Created 2004-07-16, listed "
        "2026-06-12. Rule (1), missed only because the registrant name is the "
        "legal one"),
    "Tremor International Ltd.": (
        "Nexxen",
        "accept: own article, created 2015-08-11 as 'Tremor International', "
        "renamed Nexxen 2023. Predates the 2021-06-18 listing"),
    "Membership Collective Group Inc.": (
        "Soho House (club)",
        "accept: Soho House is the firm's core service brand -- it later "
        "renamed itself Soho House & Co. Article from 2007-04-14 against a "
        "2021-07-15 listing. NOTE: this FAILED the automatic lead-mention gate "
        "(no shared token), and is accepted on the same grounds the paper "
        "accepts NYMEX Holdings -> 'New York Mercantile Exchange'. It is the "
        "case that shows why the gate cannot be the final word"),
    # Rejected: a person, not the firm. The paper's rules cover parent,
    # subsidiary, predecessor, separated-from and product -- not founders.
    "CS Disco, Inc.": (None, "reject: Kiwi Camara is the founder, a person"),
    # Rejected: generic or coincidental token.
    "Honest Company, Inc.": (None, "reject: 'Honesty' is moral character"),
    "MIDWEST HOLDING INC.": (None, "reject: 'Midwestern United States' is a region"),
    "LIZHI INC.": (None, "reject: 'Lychee' is a fruit"),
    "VIA optronics AG": (None, "reject: matched only on 'via'; Integrated "
                               "Micro-Electronics is an unrelated firm"),
    "ANCHIANO THERAPEUTICS LTD.": (None, "reject: a list article, not the firm"),
    # Rejected: article postdates the listing.
    "Kanzhun Ltd": (None, "reject: is the firm, but 'Boss Zhipin' created "
                          "2024, listed 2021"),
    "Brera Holdings PLC": (None, "reject: 'Solmate' created 2024, listed 2023"),
    "Flywire Corp": (None, "reject: disambiguation page, created 2022, listed 2021"),
    "C3.ai, Inc.": (None, "reject: 'C3 AI' created 2023, listed 2020; the union "
                          "resolver already reaches this article by other means"),
    # --- replication cohort (2006-2016), reviewed blind ---
    "RAPID7, INC.": ("Metasploit",
                     "accept: Metasploit is Rapid7's flagship product, acquired "
                     "2009, article from 2006-01-25, listed 2015-07-17. Rule (6), "
                     "and the paper's own Neurometrix -> 'Quell' pattern"),
    "FIREEYE, INC.": ("Trellix",
                      "accept: the firm's OWN article, created 2009-01-25 as "
                      "'FireEye' and moved to 'Trellix' in 2022; a page move "
                      "carries its revision history, so the 2009 date is "
                      "FireEye's own. Predates the 2013-09-20 listing"),
    "RUBICON PROJECT, INC.": ("Magnite Inc",
                              "accept: same pattern -- created 2010-11-16 as "
                              "'Rubicon Project', renamed Magnite 2020. "
                              "Predates the 2014-04-02 listing"),
    # Rejected. Name coincidence: the firm's token appears in an unrelated
    # article's alternate-name list, which is precisely what the lead-mention
    # guard cannot distinguish on its own.
    "MISTRAS GROUP, INC.": (None, "reject: 'Mystras' is a fortified town in "
                                  "Greece; 'also known as Mistras'"),
    "NOVAN, INC.": (None, "reject: 'Novin' is a village in Iran; 'also known "
                          "as Novan'"),
    "MINDBODY, INC.": (None, "reject: 'Mind-body' is a philosophy "
                             "disambiguation page, not the company"),
    # Rejected on a generic token that STOPWORDS does not cover.
    "SOLTA MEDICAL INC": (None, "reject: matched only on 'medical'. Bausch "
                                "Health acquired Solta in 2014, eight years "
                                "after the 2006-11-10 listing"),
    # Rejected on the cutoff, not on subject: genuinely the company's article,
    # created 2007-05-10 against a 2007-07-20 listing -- 71 days, inside the
    # 90-day window. The paper's endogeneity guard, working as intended.
    "HHGREGG, INC.": (None, "reject: real article, but created 71 days before "
                            "listing -- inside the cutoff"),
    # Rejected: article postdates the listing, so it cannot be pre-IPO
    # awareness whatever its subject.
    "COLFAX CORP": (None, "reject: 'Enovis' created 2013, listed 2008"),
    "FLUIDIGM CORP": (None, "reject: 'Standard BioTools' created 2015, listed 2011"),
    "TEAM HEALTH HOLDINGS INC.": (None, "reject: 'TeamHealth' created 2015, "
                                        "listed 2009"),
    "OPNEXT INC": (None, "reject: 'Oclaro' created 2008, listed 2007"),
    "HOME LOAN SERVICING SOLUTIONS, LTD.": (None, "reject: 'Rithm Capital' "
                                                  "created 2023, listed 2012"),
    "SEMLER SCIENTIFIC, INC.": (None, "reject: 'Strive Asset Management' "
                                      "created 2025, listed 2014"),
    # Rejected: an acquirer or parent that had no relation to the firm at IPO.
    "BARE ESCENTUALS INC": (None, "reject: Shiseido acquired Bare Escentuals "
                                  "in 2010, after the 2006 listing"),
    "METALDYNE PERFORMANCE GROUP INC.": (None, "reject: Masco is not the firm; "
                                               "no lead mention"),
    "NORCRAFT COMPANIES, INC.": (None, "reject: Fortune Brands acquired "
                                       "Norcraft in 2015, after listing"),
    "SYNLOGIC, INC.": (None, "reject: AbbVie is a collaborator, not the firm"),
    "VOCERA COMMUNICATIONS, INC.": (None, "reject: Stryker acquired Vocera in "
                                          "2022, ten years after listing"),
    "GALAPAGOS NV": (None, "reject: the Galapagos Islands"),
}

ARTICLES_UNION_JSON = RAW_NOTABILITY / "articles_union.json"


def merge_resolvers(cohort: str = "recent") -> dict:
    """Union the two resolvers, then apply the hand-reviewed relation verdicts.

    The two are **complementary, not nested**, which is why the union is the
    right treatment set rather than either alone:

      * Exact-title matching finds articles whose title carries extra words
        ("JOANN Inc." -> "JoAnn Fabrics", "Immunocore Holdings plc" ->
        "Immunocore") that normalised-name equality rejects.
      * Wikidata search finds articles the title builder cannot construct --
        capitalisation ("10X Genomics, Inc." -> "10x Genomics"), punctuation
        ("C3.ai, Inc." -> "C3 AI"), parenthetical disambiguators ("CHEWY, INC."
        -> "Chewy (company)"), renames ("GSX TECHEDU INC." -> "Gaotu Techedu")
        and legal-name articles ("Gitlab Inc." -> "GitLab Inc.").

    Measured on the same 500 companies: 110 strict, 105 extended, 84 agreeing,
    **131 in union** -- 26%, against the published study's 34% on a 2006-2016
    sample with manual verification throughout.
    """
    paths = cohort_paths(cohort)
    strict = (json.loads(paths["articles"].read_text())["companies"]
              if paths["articles"].exists() else {})
    ext = (json.loads(paths["articles_ext"].read_text())["companies"]
           if paths["articles_ext"].exists() else {})
    if not strict and not ext:
        raise SystemExit("run --resolve and --resolve-extended first.")

    out: dict[str, dict] = {}
    for name in set(strict) | set(ext):
        a, b = strict.get(name, {}), ext.get(name, {})
        # Prefer whichever resolver found the firm's own article; if both did and
        # they disagree, keep the strict title, which is the more literal match.
        title = a.get("title") or b.get("title")
        source = ("both" if a.get("has_article") and b.get("has_article")
                  else "strict" if a.get("has_article")
                  else "extended" if b.get("has_article") else None)
        created = a.get("article_created") or b.get("article_created")
        rec = {"company": name, "symbol": a.get("symbol") or b.get("symbol"),
               "title": title, "has_article": bool(title), "source": source,
               "rule": b.get("rule") if source in ("extended", "both") else "(1) the firm",
               "article_created": created}

        # Rule (6) verdicts apply to firms with NO PRE-IPO article, which
        # includes firms that do have one dated after the cutoff -- Rapid7's own
        # article is from 2026 and its product article from 2006. So this runs on
        # `rec` regardless of has_article and lets `build()` re-date it.
        if name in REVIEWED_PRODUCTS:
            accepted, why = REVIEWED_PRODUCTS[name]
            rec.setdefault("notes", []).append(why)
            if accepted:
                rec.update(title=accepted, has_article=True,
                           source="hand-reviewed product", rule="(6) reviewed",
                           article_created=None)
        if name in REVIEWED_RELATIONS and not rec["has_article"]:
            accepted, why = REVIEWED_RELATIONS[name]
            rec["review_verdict"] = why
            if accepted:
                rec.update(title=accepted, has_article=True,
                           source="hand-reviewed relation", rule="(2)-(6) reviewed")
        out[name] = rec

    # Hand-reviewed titles arrive without a creation date, and a missing date
    # fails the endogeneity cutoff -- which would silently drop exactly the
    # companies the review was done to rescue.
    missing = [v for v in out.values() if v["has_article"] and not v["article_created"]]
    if missing:
        import httpx

        from research.collect.config import get_research_settings
        ua = get_research_settings().sec_user_agent
        with httpx.Client(timeout=45.0, headers={"User-Agent": ua}) as c:
            for rec in missing:
                try:
                    d = c.get(WIKI_API, params={
                        "action": "query", "prop": "revisions", "titles": rec["title"],
                        "rvdir": "newer", "rvlimit": 1, "rvprop": "timestamp",
                        "format": "json"}).json()
                except Exception:
                    continue
                for page in ((d.get("query") or {}).get("pages") or {}).values():
                    revs = page.get("revisions") or []
                    if revs:
                        rec["article_created"] = revs[0].get("timestamp")
        logger.info("merged: fetched %d missing creation dates", len(missing))

    result = {"merged_at": datetime.now(UTC).isoformat(),
              "method": "union of exact-title and Wikidata rule (1), plus "
                        "hand-reviewed rule (2)-(6) verdicts",
              "companies": out}
    paths["union"].write_text(json.dumps(result, indent=1) + "\n")
    n = sum(1 for v in out.values() if v["has_article"])
    logger.info("merged: %d of %d companies have an article (%.0f%%)", n, len(out),
                100 * n / max(1, len(out)))
    return result




PREREG_JSON = DATA / "notability_prereg.json"

# The regression specification, FIXED BEFORE the outcome data is complete.
#
# Written down and timestamped because the price collection takes ~14 hours: a
# specification chosen after seeing the result is not a test of anything, and
# this module's whole argument is that the n=12 -> n=35 attenuation elsewhere in
# the project happened because a number looked convincing before it was
# stable. Changing anything here after the data lands must be recorded as a
# second, exploratory specification rather than an edit to this one.
SPEC = {
    "dependent": "underpricing = d1_close / offer_price - 1",
    "treatment": "notable_pre_ipo (1 if a Wikipedia article existed before "
                 "listing minus CUTOFF_DAYS, else 0)",
    "controls": ["log(deal_usd)", "log(offer_price)",
                 "offering-year fixed effects", "exchange fixed effects"],
    "standard_errors": "clustered by offering year-quarter, matching the "
                       "published study's stated clustering",
    "winsorization": "underpricing winsorised at the 1st and 99th percentiles; "
                     "the raw-sample result is reported alongside, and neither "
                     "is chosen after the fact",
    "primary_estimate": "the coefficient on notable_pre_ipo",
    "one_sided": False,
    "not_reproducible_from_this_data": [
        "offer price revision (no filed ranges join to priced rows)",
        "SIC industry (delisted 2006-16 tickers absent from company_tickers.json)",
        "underwriter rank (needs 424B4 cover parsing)",
        "firm age, VC backing (need paid sources)",
        "propensity score matching and the instrumental variable",
        "analyst following and institutional ownership (I/B/E/S, 13F)",
    ],
}


def _ols_cluster(y, X, clusters, names):
    """OLS with cluster-robust standard errors. No statsmodels dependency.

    Hand-rolled for the same reason `_mann_whitney` is: adding statsmodels for
    one regression is not worth a new dependency in a project whose brief says
    to ask before adding any. The estimator is textbook -- CRVE with the usual
    finite-sample correction G/(G-1) * (n-1)/(n-k) -- and every step is visible.
    """
    import numpy as np

    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape
    xtx = X.T @ X
    if np.linalg.matrix_rank(xtx) < k:
        raise SystemExit("design matrix is rank-deficient; drop a collinear column")
    xtx_inv = np.linalg.inv(xtx)
    beta = xtx_inv @ (X.T @ y)
    resid = y - X @ beta

    groups = np.asarray(clusters)
    meat = np.zeros((k, k))
    for g in np.unique(groups):
        m = groups == g
        xu = X[m].T @ resid[m]
        meat += np.outer(xu, xu)
    G = len(np.unique(groups))
    scale = (G / max(G - 1, 1)) * ((n - 1) / max(n - k, 1))
    vcv = xtx_inv @ meat @ xtx_inv * scale
    se = np.sqrt(np.clip(np.diag(vcv), 0, None))

    from math import erf, sqrt
    out = []
    for name, b, s in zip(names, beta, se):
        t = b / s if s > 0 else float("nan")
        p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2)))) if s > 0 else float("nan")
        out.append({"term": name, "coef": float(b), "se": float(s),
                    "t": float(t), "p": float(p)})
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"terms": out, "n": n, "k": k, "clusters": G,
            "r2": 1 - ss_res / ss_tot if ss_tot else float("nan")}


def regress(*, cohort: str = "recent", winsorize: bool = True,
            cutoff_days: int = CUTOFF_DAYS) -> dict:
    """The pre-registered specification. Prints a coefficient table.

    Reports the conditional estimate alongside the raw group difference, because
    the published study's headline is a regression coefficient with controls
    while this module's earlier result was a raw median comparison -- and those
    are not the same test. If conditioning is what reveals the effect, this is
    where it shows up.
    """
    import numpy as np
    import pandas as pd

    if not PREREG_JSON.exists():
        PREREG_JSON.write_text(json.dumps(
            {"registered_at": datetime.now(UTC).isoformat(), "spec": SPEC},
            indent=2) + "\n")
        logger.info("pre-registration written -> %s", PREREG_JSON.name)

    d = build(cutoff_days=cutoff_days, cohort=cohort)
    d = d.dropna(subset=["underpricing", "deal_usd", "offer_price"]).copy()
    d = d[(d["deal_usd"] > 0) & (d["offer_price"] > 0)]
    if len(d) < 30:
        print(f"only {len(d)} usable rows for cohort {cohort!r}; "
              f"price collection is still running.")
        return {}

    d["y"] = d["underpricing"].astype(float)
    if winsorize:
        lo, hi = d["y"].quantile([0.01, 0.99])
        d["y"] = d["y"].clip(lo, hi)
    d["treat"] = d["notable_pre_ipo"].astype(int)
    d["log_deal"] = np.log(d["deal_usd"].astype(float))
    d["log_price"] = np.log(d["offer_price"].astype(float))
    d["yq"] = (pd.to_datetime(d["listing_date"]).dt.year.astype(str) + "Q"
               + pd.to_datetime(d["listing_date"]).dt.quarter.astype(str))

    # drop_first avoids the dummy trap; a rank check in _ols_cluster catches
    # anything left collinear.
    year_fe = pd.get_dummies(pd.to_datetime(d["listing_date"]).dt.year,
                             prefix="yr", drop_first=True, dtype=float)
    exch = d["exchange"].fillna("unknown").str.replace(r"\s+", "_", regex=True)
    exch_fe = pd.get_dummies(exch, prefix="ex", drop_first=True, dtype=float)

    design = pd.concat([
        pd.Series(1.0, index=d.index, name="const"),
        d[["treat", "log_deal", "log_price"]].astype(float),
        year_fe, exch_fe], axis=1)
    # Constant-within-sample dummies carry no information and break the rank check.
    design = design.loc[:, design.nunique() > 1].copy()
    design.insert(0, "const", 1.0)

    res = _ols_cluster(d["y"].values, design.values, d["yq"].values,
                       list(design.columns))

    t = d.loc[d["treat"] == 1, "y"] * 100
    c = d.loc[d["treat"] == 0, "y"] * 100
    print(f"=== cohort {cohort}: pre-registered OLS ===")
    print(f"  n={res['n']}  regressors={res['k']}  year-quarter clusters="
          f"{res['clusters']}  R2={res['r2']:.3f}"
          f"{'  (winsorised 1/99)' if winsorize else '  (raw)'}\n")
    print(f"  raw group medians: treated {t.median():+.1f}%  (n={len(t)})   "
          f"control {c.median():+.1f}%  (n={len(c)})\n")
    print(f"  {'term':16}{'coef':>10}{'se':>9}{'t':>8}{'p':>9}")
    for row in res["terms"]:
        if row["term"].startswith(("yr_", "ex_")):
            continue          # fixed effects estimated, not reported
        star = ("***" if row["p"] < 0.01 else "**" if row["p"] < 0.05
                else "*" if row["p"] < 0.10 else "")
        print(f"  {row['term']:16}{row['coef']:>10.4f}{row['se']:>9.4f}"
              f"{row['t']:>8.2f}{row['p']:>9.4f} {star}")
    print(f"\n  (year and exchange fixed effects estimated and not shown)")
    tr = next(r for r in res["terms"] if r["term"] == "treat")
    print(f"\n  PRIMARY: a pre-IPO Wikipedia article is associated with "
          f"{tr['coef']*100:+.1f} percentage points of underpricing, "
          f"p = {tr['p']:.4f}")
    print("  " + ("significant at 0.05" if tr["p"] < 0.05
                  else "NOT significant at 0.05"))
    return res


# --------------------------------------------------------------------------
# rule (6): the firm's core product or service, via its own redirect
# --------------------------------------------------------------------------

ARTICLES_PROD_JSON = RAW_NOTABILITY / "articles_products.json"


def _lead_mentions(lead: str, company: str) -> list[str]:
    """Distinctive company tokens that appear in the target article's lead.

    The precision guard for rule (6), and the whole reason this pass can be
    trusted at all. A redirect tells us Wikipedia routes the company's name
    somewhere; it does not tell us the destination is *about* the company.
    "Portola" redirects to a town in California. Requiring the firm's own
    distinctive token in the destination's opening paragraph separates
    "Metasploit ... maintained by Rapid7" from that.
    """
    toks = {w for w in re.findall(r"[a-z0-9]+", (company or "").casefold())
            if w not in STOPWORDS and len(w) > 2}
    low = (lead or "").casefold()
    return sorted(t for t in toks if t in low)


async def resolve_articles_products(*, refetch: bool = False,
                                    cohort: str = "recent",
                                    cutoff_days: int = CUTOFF_DAYS) -> dict:
    """Rule (6) candidates: where the firm's own name redirects elsewhere.

    The strict resolver already requests titles with ``redirects=1``, so it
    *saw* these targets and then discarded them on the distinctive-token guard
    -- `Rapid7` -> `Metasploit` shares no word with "RAPID7, INC.". That guard
    is load-bearing (it stops `Legence Corp.` -> `VF Corporation`) and cannot
    simply be removed. This pass recovers the cases it costs, using a different
    and much stronger signal:

    **A redirect from the company's name is Wikipedia's own editorial
    assertion about where that company is covered.** Nobody creates
    `Rapid7` -> `Metasploit` by accident. That is exactly the paper's rule (6),
    whose own examples defeat every name test: NYMEX Holdings ->
    "New York Mercantile Exchange", Intersections Inc. -> "Identity Guard",
    Neurometrix -> "Quell".

    Three gates, each rejecting a specific observed failure:

      * the destination's lead must contain a distinctive token of the firm
        (`_lead_mentions`) -- "Portola Pharmaceuticals" also redirects toward a
        Californian town;
      * the destination must clear ``MIN_ARTICLE_WORDS``, the paper's own
        stub rule;
      * the destination's **first revision** is recorded, because a rule (6)
        article is only treatment if it predates the IPO. Metasploit's article
        (2006) predates Rapid7's 2015 listing; a product page written after the
        listing is not pre-IPO investor awareness.

    Candidates are written for review and **never auto-accepted**, matching
    `RELATION_RULES` and the paper's hand-verification. Precision matters more
    than recall here: a false positive flips an observation between groups
    rather than merely adding noise.
    """
    import pandas as pd

    from backend.http import RetryingClient
    from research.collect.config import get_research_settings

    ensure_dirs()
    RAW_NOTABILITY.mkdir(parents=True, exist_ok=True)
    paths = cohort_paths(cohort)
    sfx = "" if cohort == "recent" else f"_{cohort}"
    out_path = RAW_NOTABILITY / f"articles_products{sfx}.json"
    if out_path.exists() and not refetch:
        logger.info("products: using cached %s", out_path.name)
        return json.loads(out_path.read_text())

    sample = pd.read_parquet(paths["sample"])
    # Skip only firms already in TREATMENT -- an article that predates the
    # listing by the cutoff. Mere existence is the wrong filter and excluded the
    # motivating case: Rapid7 has an article of its own, created 2026, eleven
    # years AFTER its 2015 listing, so it is not pre-IPO treatment. Its product
    # article (Metasploit, 2006) is. Firms whose own article arrived late are
    # exactly the population rule (6) exists to rescue.
    pre_ipo: set[str] = set()
    if paths["union"].exists():
        listed = {r["name"]: pd.to_datetime(r["listing_date"], errors="coerce")
                  for _, r in sample.iterrows()}
        for k, v in json.loads(
                paths["union"].read_text())["companies"].items():
            if not v.get("has_article"):
                continue
            created = pd.to_datetime(v.get("article_created"), errors="coerce",
                                     utc=True)
            listing = listed.get(k)
            if pd.isna(created) or pd.isna(listing):
                continue
            if created.tz_localize(None) <= listing - timedelta(days=cutoff_days):
                pre_ipo.add(k)
    todo = [r["name"] for _, r in sample.iterrows() if r["name"] not in pre_ipo]
    logger.info("products[%s]: %d companies, %d without pre-IPO treatment "
                "to probe", cohort, len(sample), len(todo))

    # variant title -> companies that proposed it
    # `candidate_titles`, NOT `name_variants`. The latter feeds Wikidata's
    # case-insensitive search; MediaWiki titles are case-sensitive after the
    # first character, so the registrant's all-caps "RAPID7" is a different
    # (nonexistent) title from "Rapid7" and the redirect is never seen.
    # `candidate_titles` emits the title-cased form for exactly this reason.
    proposals: dict[str, list[str]] = {}
    for name in todo:
        for v in candidate_titles(name):
            proposals.setdefault(v, []).append(name)
    titles = sorted(proposals)

    settings = get_research_settings()
    client = RetryingClient(user_agent=settings.sec_user_agent, per_second=4.0,
                            max_retries=3, base_backoff=3.0)

    # Phase 1 -- batched: which requested titles are redirects, and to where.
    hops: dict[str, str] = {}
    batches_ok = batches_failed = 0
    try:
        for i in range(0, len(titles), 50):
            batch = titles[i:i + 50]
            try:
                payload = await client.get_json(WIKI_API, params={
                    "action": "query", "titles": "|".join(batch),
                    "redirects": 1, "format": "json"})
            except Exception as exc:
                logger.warning("products: batch %d failed: %s", i // 50,
                               type(exc).__name__)
                batches_failed += 1
                continue
            batches_ok += 1
            q = payload.get("query") or {}
            for m in q.get("redirects") or []:
                hops[m["from"]] = m["to"]
        logger.info("products: %d/%d batches ok, %d redirects seen",
                    batches_ok, batches_ok + batches_failed, len(hops))

        # Keep only the redirects the strict resolver could not use: a target
        # sharing a distinctive token was already reachable by exact title.
        interesting: dict[str, list[str]] = {}
        for src, dst in hops.items():
            for company in proposals.get(src, []):
                if shares_distinctive_token(company, dst):
                    continue
                interesting.setdefault(dst, []).append(company)
        logger.info("products: %d distinct targets the token guard rejected",
                    len(interesting))

        # Phase 2 -- per target: lead text, size and first revision. `rvlimit`
        # cannot be combined with multiple titles, so this is one call each.
        out: dict[str, dict] = {}
        for n, (target, companies) in enumerate(sorted(interesting.items()), 1):
            try:
                info = await client.get_json(WIKI_API, params={
                    "action": "query", "titles": target, "prop": "extracts",
                    "exintro": 1, "explaintext": 1, "format": "json"})
                page = next(iter((info.get("query") or {})
                                 .get("pages", {}).values()), {})
                lead = page.get("extract") or ""
                rev = await client.get_json(WIKI_API, params={
                    "action": "query", "titles": target, "prop": "revisions",
                    "rvdir": "newer", "rvlimit": 1, "rvprop": "timestamp",
                    "format": "json"})
                rpage = next(iter((rev.get("query") or {})
                                  .get("pages", {}).values()), {})
                created = ((rpage.get("revisions") or [{}])[0]
                           .get("timestamp"))
            except Exception as exc:
                logger.warning("products: %s failed: %s", target,
                               type(exc).__name__)
                continue
            words = len(re.findall(r"[A-Za-z0-9']+", lead))
            for company in companies:
                hits = _lead_mentions(lead, company)
                out.setdefault(company, {"company": company, "candidates": []})
                out[company]["candidates"].append({
                    "rule": "(6) core product or service, via redirect",
                    "target": target,
                    "article_created": created,
                    "lead_words": words,
                    "company_tokens_in_lead": hits,
                    # Every gate the paper applies, evaluated but not enforced:
                    # the verdict is a human's.
                    "passes_lead_mention": bool(hits),
                    "passes_min_words": words >= MIN_ARTICLE_WORDS,
                    "lead": lead[:400],
                })
            if n % 25 == 0:
                logger.info("products: %d/%d targets", n, len(interesting))
    finally:
        await client.aclose()

    blob = {
        "resolved_at": datetime.now(UTC).isoformat(),
        "cohort": cohort,
        "method": ("rule (6) candidates from redirects out of the firm's own "
                   "name to a differently-titled article. Collected for review, "
                   "never auto-accepted."),
        "companies_probed": len(todo),
        "redirects_seen": len(hops),
        "companies": out,
    }
    out_path.write_text(json.dumps(blob, indent=1) + "\n")
    strong = sum(1 for v in out.values()
                 if any(c["passes_lead_mention"] and c["passes_min_words"]
                        for c in v["candidates"]))
    logger.info("products[%s]: %d companies with a candidate, %d passing both "
                "gates -> %s", cohort, len(out), strong, out_path.name)
    return blob


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
