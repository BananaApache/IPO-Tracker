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


def eligible():
    """The population, before sampling. No outcome variable is touched here."""
    import pandas as pd

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
    keep["deal_usd"] = pd.to_numeric(keep["total_shares_value"], errors="coerce")
    keep["size_bucket"] = pd.cut(keep["deal_usd"],
                                 [0, 5e7, 2e8, 1e9, float("inf")],
                                 labels=["<50M", "50-200M", "200M-1B", ">1B"])
    logger.info("eligible: %d of %d priced rows (%d SPAC-like, %d under $%.0f)",
                len(keep), len(p), int(p["is_spac"].sum()),
                int((~p["is_spac"] & (p["offer_price"] < MIN_OFFER_PRICE)).sum()),
                MIN_OFFER_PRICE)
    return keep


def build_sample(size: int, seed: int = 0):
    """Stratified random draw by year x deal-size bucket.

    Stratified so the sample keeps the population's shape on the two dimensions
    underpricing is most known to vary on. Deterministic seed, written to disk
    before any price or article is fetched, so the cohort cannot be reshuffled
    after seeing a result.
    """
    import pandas as pd

    pop = eligible().dropna(subset=["size_bucket"])
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
    out.to_parquet(SAMPLE_PARQUET, index=False)
    logger.info("sample: %d rows -> %s", len(out), SAMPLE_PARQUET)
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


async def resolve_articles(*, refetch: bool = False) -> dict:
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
    if ARTICLES_JSON.exists() and not refetch:
        logger.info("articles: using cached %s", ARTICLES_JSON)
        return json.loads(ARTICLES_JSON.read_text())

    sample = pd.read_parquet(SAMPLE_PARQUET)
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
    ARTICLES_JSON.write_text(json.dumps(result, indent=1) + "\n")
    logger.info("articles: -> %s", ARTICLES_JSON)
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


async def collect_prices(*, batch: int = 0, per_hour: int = TIINGO_PER_HOUR) -> dict:
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

    sample = pd.read_parquet(SAMPLE_PARQUET)
    have = json.loads(PRICES_JSON.read_text()) if PRICES_JSON.exists() else {}
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
                    PRICES_JSON.write_text(json.dumps(have, indent=1) + "\n")
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
                # Recorded as an explicit miss so a resume does not retry it
                # forever, and so the build step can count it.
                have[sym] = {"symbol": sym, "bars": [], "note": "no bars returned"}
                fetched += 1
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
            PRICES_JSON.write_text(json.dumps(have, indent=1) + "\n")
            if fetched % 25 == 0:
                logger.info("  prices: %d fetched, %d cached total", fetched,
                            len(have))
    finally:
        await client.aclose()
        PRICES_JSON.write_text(json.dumps(have, indent=1) + "\n")
    logger.info("prices: %d fetched, %d failed, %d total cached -> %s",
                fetched, failed, len(have), PRICES_JSON)
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


def build(*, cutoff_days: int = CUTOFF_DAYS):
    """Join sample + articles + prices into the analysis table.

    Treatment is `has_article AND article_created <= listing_date -
    cutoff_days`. The cutoff stands in for "before the public knew": the census
    carries no S-1 dates, and measured on the watchlist the public S-1 precedes
    listing by a median of 27 days (90th percentile 74), so 90 days clears the
    registration window for roughly 93% of deals. An article created inside that
    window is plausibly an effect of the IPO rather than prior notability.
    """
    import pandas as pd

    sample = pd.read_parquet(SAMPLE_PARQUET)
    arts = (json.loads(ARTICLES_JSON.read_text())["companies"]
            if ARTICLES_JSON.exists() else {})
    prices = json.loads(PRICES_JSON.read_text()) if PRICES_JSON.exists() else {}

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
    df.to_parquet(NOTABILITY_PARQUET, index=False)
    logger.info("notability: %d rows (%d with a price, %d notable pre-IPO) -> %s",
                len(df), int(df["underpricing"].notna().sum()),
                int(df["notable_pre_ipo"].sum()), NOTABILITY_PARQUET)
    return df


def analyse(*, cutoff_days: int = CUTOFF_DAYS) -> None:
    """Group comparison, then the same comparison inside strata."""
    import numpy as np
    import pandas as pd

    df = build(cutoff_days=cutoff_days)
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
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--prices", action="store_true",
                    help="day-1 prices from Tiingo, paced to the hourly allocation")
    ap.add_argument("--batch", type=int, default=0,
                    help="stop after this many price requests")
    ap.add_argument("--per-hour", type=int, default=TIINGO_PER_HOUR)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--analyse", action="store_true")
    ap.add_argument("--cutoff-days", type=int, default=CUTOFF_DAYS,
                    help="an article must predate listing minus this many days")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)

    if args.sample:
        build_sample(args.sample, seed=args.seed)
    if args.resolve:
        await resolve_articles(refetch=args.refetch)
    if args.prices:
        await collect_prices(batch=args.batch, per_hour=args.per_hour)
    if args.analyse:
        analyse(cutoff_days=args.cutoff_days)
    elif args.build:
        build(cutoff_days=args.cutoff_days)
    if not any((args.sample, args.resolve, args.prices, args.build, args.analyse)):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
