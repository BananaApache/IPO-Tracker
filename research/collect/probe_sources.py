"""Measure what each source actually delivers, and write it down.

Every source in the module brief advertises something its free tier does not
necessarily grant. Rather than discover that halfway through a collection run,
this script asks each one a small number of deliberately chosen questions and
records the answers to `research/data/source_probe.json`.

The output is the evidence behind the source table in `research/README.md`. Rerun
it when a provider is suspected of having changed; the committed JSON carries the
date each measurement was taken, because "the free tier reaches back 30 days" is
a fact with a shelf life.

Run:  uv run --group research python -m research.collect.probe_sources
"""

import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from research.collect.config import ResearchSettings, get_research_settings
from research.collect.paths import SOURCE_PROBE_JSON, ensure_dirs


@dataclass
class Probe:
    """One question asked of one provider, and what came back."""

    source: str
    question: str
    request: str
    verdict: str           # ok | gated | empty | error
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> httpx.Response:
    return await client.get(url, params=params, timeout=30.0)


async def probe_finnhub(client: httpx.AsyncClient, s: ResearchSettings) -> list[Probe]:
    out: list[Probe] = []

    url = "https://finnhub.io/api/v1/calendar/ipo"
    params = {"from": "2024-03-01", "to": "2024-03-31", "token": s.finnhub_key}
    r = await _get(client, url, params)
    if r.status_code == 200 and isinstance(r.json().get("ipoCalendar"), list):
        rows = r.json()["ipoCalendar"]
        out.append(Probe(
            "finnhub", "Does the IPO calendar return a windowed month?",
            "GET /api/v1/calendar/ipo?from=2024-03-01&to=2024-03-31", "ok",
            f"{len(rows)} rows for 2024-03.",
            {"row_count": len(rows),
             "fields": sorted(rows[0].keys()) if rows else [],
             "statuses": sorted({r_.get("status") for r_ in rows if r_.get("status")})},
        ))
    else:
        out.append(Probe("finnhub", "Does the IPO calendar return a windowed month?",
                         "GET /api/v1/calendar/ipo", "error",
                         f"HTTP {r.status_code}: {r.text[:160]}"))

    # Asked separately because the calendar and the price history are different
    # entitlements on the same key, and the module needs both.
    r = await _get(client, "https://finnhub.io/api/v1/stock/candle",
                   {"symbol": "RDDT", "resolution": "D", "from": 1711000000,
                    "to": 1714000000, "token": s.finnhub_key})
    gated = r.status_code in (401, 403)
    out.append(Probe(
        "finnhub", "Can this key read historical daily OHLC?",
        "GET /api/v1/stock/candle?symbol=RDDT&resolution=D", "gated" if gated else "ok",
        f"HTTP {r.status_code}: {r.text[:120]}",
    ))
    return out


async def probe_nyt(client: httpx.AsyncClient, s: ResearchSettings) -> list[Probe]:
    """NYT: where the hit count lives, whether fq works, and how far back it reaches."""
    out: list[Probe] = []
    url = "https://api.nytimes.com/svc/search/v2/articlesearch.json"

    async def hits(params: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
        # 5 requests/minute. Spacing is the collector's job in nyt.py; here the
        # probe is small enough to just wait.
        await asyncio.sleep(13)
        r = await _get(client, url, {**params, "api-key": s.nyt_key})
        if r.status_code != 200:
            return None, {"http": r.status_code, "body": r.text[:140]}
        body = r.json()
        resp = body.get("response") or {}
        # The brief said `response.meta.hits`. The live envelope has no `meta`
        # key at all -- the count is under `response.metadata.hits`.
        return (resp.get("metadata") or {}).get("hits"), {
            "has_meta_key": "meta" in resp,
            "has_metadata_key": "metadata" in resp,
            "returned_docs": len(resp.get("docs") or []),
        }

    n, ev = await hits({"q": '"Instacart"', "begin_date": "20230901", "end_date": "20230930"})
    out.append(Probe("nyt", "Where is the hit count, and does a known window return one?",
                     'GET articlesearch q="Instacart" 2023-09',
                     "ok" if n else "error",
                     f"hits={n} at response.metadata.hits; response.meta absent={not ev.get('has_meta_key')}",
                     ev | {"hits": n}))

    # The brief recommends fq/Lucene for precision. Measured, not assumed.
    n_fq, _ = await hits({"fq": 'organizations:("Instacart")',
                          "begin_date": "20230901", "end_date": "20230930"})
    out.append(Probe("nyt", "Does fielded fq (Lucene) narrow a query, or return nothing?",
                     'GET articlesearch fq=organizations:("Instacart") 2023-09',
                     "ok" if n_fq else "empty",
                     f"hits={n_fq}. Fielded fq returns 0 where the same term as q returns "
                     f"{n}; treat fq as unavailable on this key and use q only.",
                     {"hits": n_fq}))

    n_2019, _ = await hits({"q": '"Instacart"', "begin_date": "20190101", "end_date": "20190630"})
    out.append(Probe("nyt", "Does history reach 2019, the start of the study window?",
                     'GET articlesearch q="Instacart" 2019-01..06',
                     "ok" if n_2019 else "empty", f"hits={n_2019} for 2019 H1.",
                     {"hits": n_2019}))

    # Ambiguity, the reason the query form has to be frozen before collection.
    n_bare, _ = await hits({"q": '"Circle"', "begin_date": "20250601", "end_date": "20250630"})
    n_phrase, _ = await hits({"q": '"Circle" "stablecoin"',
                              "begin_date": "20250601", "end_date": "20250630"})
    out.append(Probe("nyt", "How much noise does an ordinary-word brand pull in?",
                     'GET articlesearch q="Circle" vs q="Circle" "stablecoin", 2025-06',
                     "ok", f'q="Circle" -> {n_bare} hits; q="Circle" "stablecoin" -> '
                           f"{n_phrase}. Implicit AND of quoted phrases works and is the "
                           f"only precision lever available without fq.",
                     {"bare_hits": n_bare, "phrase_hits": n_phrase}))
    return out


async def probe_gnews(client: httpx.AsyncClient, s: ResearchSettings) -> list[Probe]:
    out: list[Probe] = []
    url = "https://gnews.io/api/v4/search"
    r = await _get(client, url, {"q": '"Instacart"', "from": "2023-01-01T00:00:00Z",
                                 "to": "2023-03-31T00:00:00Z", "max": 10,
                                 "apikey": s.gnews_key})
    if r.status_code != 200:
        out.append(Probe("gnews", "Does a historical window return articles?",
                         "GET /api/v4/search from=2023-01-01", "error",
                         f"HTTP {r.status_code}: {r.text[:160]}"))
        return out
    body = r.json()
    removed = (body.get("articlesRemovedFromResponse") or {}).get("historicalArticles")
    out.append(Probe(
        "gnews", "Does the free tier return articles for a 2023 window?",
        'GET /api/v4/search q="Instacart" 2023-01..03',
        "gated" if removed else "ok",
        f"totalArticles={body.get('totalArticles')} but articles=[] -- "
        f"{(removed or {}).get('message', 'no notice')}",
        {"totalArticles": body.get("totalArticles"),
         "returned_articles": len(body.get("articles") or []),
         "notice": (removed or {}).get("message"),
         "realtime_notice": ((body.get("information") or {})
                             .get("realTimeArticles") or {}).get("message")},
    ))
    return out


async def probe_twitter(client: httpx.AsyncClient, s: ResearchSettings) -> list[Probe]:
    """The question that decides whether X is usable: does it reach 2019?"""
    out: list[Probe] = []
    url = "https://api.twitterapis.com/twitter/tweet/advanced_search"
    headers = {"Authorization": f"Bearer {s.twitter_key}"}

    for label, query in (
        ("2019", '"Instacart" since:2019-01-01 until:2019-01-31'),
        ("2026", '"Figma" since:2026-08-01 until:2026-08-31'),
    ):
        r = await client.get(url, params={"query": query}, headers=headers, timeout=30.0)
        if r.status_code != 200:
            out.append(Probe("twitterapis", f"Does search reach {label}?",
                             f"GET /twitter/tweet/advanced_search {query}", "error",
                             f"HTTP {r.status_code}: {r.text[:140]}"))
            continue
        body = r.json()
        tweets = body.get("tweets") or []
        out.append(Probe(
            "twitterapis", f"Does search reach {label}?",
            f"GET /twitter/tweet/advanced_search {query}",
            "ok" if tweets else "empty",
            f"{len(tweets)} tweets, next_cursor present={bool(body.get('next_cursor'))}. "
            f"No total-count field: a monthly count requires full pagination.",
            {"returned": len(tweets), "count_field": body.get("count"),
             "has_cursor": bool(body.get("next_cursor")),
             "sample_created_at": [t.get("created_at") for t in tweets[:3]]},
        ))
    return out


async def probe_reddit(client: httpx.AsyncClient, s: ResearchSettings) -> list[Probe]:
    """Reddit: can a dated monthly window be expressed at all?"""
    out: list[Probe] = []
    url = "https://api.redditapis.com/api/reddit/search"
    headers = {"Authorization": f"Bearer {s.reddit_key}"}

    r = await client.get(url, params={"q": "Instacart", "sort": "new", "t": "all",
                                      "limit": 100}, headers=headers, timeout=30.0)
    if r.status_code != 200:
        out.append(Probe("redditapis", "How far back does sort=new reach?",
                         "GET /api/reddit/search?q=Instacart&sort=new&t=all", "error",
                         f"HTTP {r.status_code}: {r.text[:140]}"))
        return out
    body = r.json()
    posts = body.get("posts") or []
    stamps = sorted(p["created_utc"] for p in posts if p.get("created_utc"))
    span_days = None
    if stamps:
        span_days = round((stamps[-1] - stamps[0]) / 86400.0, 2)
    out.append(Probe(
        "redditapis", "How far back does one page of sort=new reach?",
        "GET /api/reddit/search?q=Instacart&sort=new&t=all&limit=100",
        "ok" if posts else "empty",
        f"{len(posts)} posts spanning {span_days} days "
        f"({datetime.fromtimestamp(stamps[0], UTC).date() if stamps else None} to "
        f"{datetime.fromtimestamp(stamps[-1], UTC).date() if stamps else None}). "
        f"The endpoint has no from/to parameter -- only a coarse `t` bucket -- so a "
        f"dated monthly window cannot be requested, and reaching 2019 by paging "
        f"`new` is bounded by Reddit's own listing cut-off.",
        {"returned": len(posts), "span_days": span_days,
         "listing_status": body.get("listing_status"),
         "hint": (body.get("hint") or "")[:400],
         "date_range_params": "absent: only t=hour|day|week|month|year|all"},
    ))
    return out


async def probe_polygon(client: httpx.AsyncClient, s: ResearchSettings) -> list[Probe]:
    """Polygon is the licensed price feed. The question is how far back it reaches."""
    out: list[Probe] = []
    tmpl = ("https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/"
            "{d}/{d}?adjusted=false&apiKey=" + s.polygon_key)

    # Bisect the entitlement boundary on a ticker that has traded throughout, so
    # an empty answer means "not entitled", never "no such history".
    today = datetime.now(UTC).date()
    lo, hi = today - timedelta(days=365 * 6), today - timedelta(days=3)
    authorized: date | None = None
    checks: list[dict[str, Any]] = []
    for _ in range(12):
        if (hi - lo).days <= 1:
            break
        mid = lo + (hi - lo) / 2
        await asyncio.sleep(13)  # 5/min free tier
        r = await client.get(tmpl.format(d=mid.isoformat()), timeout=30.0)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        status = body.get("status")
        checks.append({"date": mid.isoformat(), "status": status})
        if status == "NOT_AUTHORIZED":
            lo = mid
        elif status in ("OK", "DELAYED"):
            hi = mid
            authorized = mid
        else:
            # A transient error must not be read as a boundary; retry lower.
            lo = mid
    out.append(Probe(
        "polygon", "How far back does the plan grant daily bars?",
        "GET /v2/aggs/ticker/AAPL/range/1/day/{d}/{d}, bisected",
        "ok" if authorized else "gated",
        f"Earliest authorized date found: {authorized}. That is "
        f"{(today - authorized).days if authorized else None} days before {today} "
        f"-- a rolling two-year entitlement, not a fixed start date.",
        {"earliest_authorized": authorized.isoformat() if authorized else None,
         "probed_on": today.isoformat(), "checks": checks},
    ))
    return out


async def main() -> int:
    ensure_dirs()
    s = get_research_settings()
    probes: list[Probe] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, fn in (
            ("finnhub", probe_finnhub), ("nyt", probe_nyt), ("gnews", probe_gnews),
            ("twitterapis", probe_twitter), ("redditapis", probe_reddit),
            ("polygon", probe_polygon),
        ):
            print(f"probing {name}...", file=sys.stderr, flush=True)
            try:
                probes.extend(await fn(client, s))
            except Exception as exc:  # a probe failing must not lose the others
                probes.append(Probe(name, "probe raised", "-", "error", repr(exc)[:200]))

    payload = {
        "probed_at": datetime.now(UTC).isoformat(),
        "note": "What each source actually delivered, measured. See research/README.md.",
        "probes": [asdict(p) for p in probes],
    }
    SOURCE_PROBE_JSON.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"wrote {SOURCE_PROBE_JSON}", file=sys.stderr)
    for p in probes:
        print(f"  {p.source:12} {p.verdict:7} {p.question}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
