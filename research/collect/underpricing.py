"""Does pre-listing Wikipedia attention predict IPO underpricing?

**Underpricing** is the classic IPO outcome: `(day-1 close - offer price) /
offer price`, the "money left on the table". It is a far better dependent
variable than the 30/90-day returns used elsewhere in this module -- measured on
day one, so there is no window to choose, and with decades of literature giving
a benchmark magnitude.

The design follows Da, Engelberg & Gao (2011), who found Google search volume
predicts IPO first-day returns. Two things are taken from that paper and one is
deliberately *not*:

  * **Attention is measured strictly before the price is set.** The offer price
    is fixed the evening before the first trade, so the event window ends the day
    before listing. Monthly pageview granularity cannot express that -- pricing
    happens mid-month -- so this collector uses **daily** pageviews.
  * **Abnormal attention, not level.** Raw views are dominated by company fame:
    SpaceX draws a million views a month whatever it is doing. The regressor is
    the event window's mean daily views divided by the company's own baseline
    (days -120 to -31). That is the paper's abnormal-SVI construction and it
    controls for size and fame without needing a separate covariate.
  * **Not** their sample size. They had thousands of IPOs. Wikipedia cannot
    supply that: 0 of 20 randomly sampled recent IPOs have an article at all,
    because Wikipedia's notability bar excludes most issuers. Google's search
    index covers every string; Wikipedia covers notable subjects. That asymmetry
    is why their design worked and why this one is confined to a hand-picked
    list of famous companies -- n=12, against the 730-day price entitlement.

So this is **underpowered by construction** and the analysis says so. It is built
because it costs nothing (Wikipedia is keyless, the prices are already collected)
and because the design is the right one to scale if a price feed with real
history is ever bought.

Run:  uv run --group research python -m research.collect.underpricing
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta

from backend.http import RetryingClient
from research.collect.config import get_research_settings
from research.collect.nyt import slug
from research.collect.paths import DATA, RAW, ensure_dirs

logger = logging.getLogger(__name__)

DAILY = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
         "en.wikipedia/all-access/user/{title}/daily/{start}/{end}")
RAW_DAILY = RAW / "wikipedia_daily"
UNDERPRICING_PARQUET = DATA / "underpricing.parquet"

# Event window: the 30 days ending the day BEFORE listing. The offer price is
# set the evening before the first trade, so anything from the listing day
# onwards is contaminated by the outcome.
EVENT_DAYS = 30
# Baseline: days -120 to -31, i.e. the three months before the event window.
BASELINE_START = 120
BASELINE_END = 31


def titles_for(company: str) -> list[str]:
    """Article plus redirects, reused from the monthly collection.

    Not re-resolved: the mapping was already audited by hand once, and resolving
    twice risks the two halves of this module disagreeing about which article a
    company is.
    """
    path = RAW / "wikipedia" / f"{slug(company)}.json"
    if not path.exists():
        return []
    blob = json.loads(path.read_text())
    by_title = blob.get("views_by_title") or {}
    if by_title:
        return list(by_title.keys())
    title = (blob.get("resolution") or {}).get("title")
    return [title] if title else []


async def fetch_daily(client: RetryingClient, title: str, start: date,
                      end: date) -> dict[str, int]:
    url = DAILY.format(title=title.replace(" ", "_"),
                       start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    try:
        payload = await client.get_json(url)
    except Exception as exc:
        logger.debug("daily pageviews: %s -> %s", title, type(exc).__name__)
        return {}
    return {i["timestamp"][:8]: int(i.get("views") or 0)
            for i in (payload.get("items") or [])}


async def collect() -> int:
    import pandas as pd

    ensure_dirs()
    RAW_DAILY.mkdir(parents=True, exist_ok=True)
    s = get_research_settings()

    wl = pd.read_csv(DATA / "watchlist.csv")
    # Tiingo rather than Polygon: it covers all 39 Tier B companies back to 2019,
    # where Polygon's 730-day entitlement reaches only 12. The two were checked
    # against each other on 1,131 overlapping sessions and agree to $0.0000, so
    # this is a coverage upgrade rather than a change of measurement.
    px = pd.read_parquet(DATA / "tiingo_daily.parquet")
    have_price = set(px["company"].unique())
    rows = [r for r in wl.to_dict("records") if r["company"] in have_price]
    logger.info("underpricing: %d companies with a day-1 price", len(rows))

    client = RetryingClient(user_agent=s.sec_user_agent, per_second=4.0,
                            max_retries=3, base_backoff=3.0)
    n = 0
    try:
        for row in rows:
            company = row["company"]
            path = RAW_DAILY / f"{slug(company)}.json"
            if path.exists():
                n += 1
                continue
            listing = date.fromisoformat(str(row["listing_date"])[:10])
            start = listing - timedelta(days=BASELINE_START + 5)
            end = listing + timedelta(days=5)

            titles = titles_for(company)
            if not titles:
                logger.warning("%s: no resolved Wikipedia title; skipped", company)
                continue
            # Summed across the article and its redirects, exactly as the monthly
            # series does -- a renamed page leaves its history under the old title.
            merged: dict[str, int] = {}
            for title in titles:
                for day, views in (await fetch_daily(client, title, start, end)).items():
                    merged[day] = merged.get(day, 0) + views

            path.write_text(json.dumps({
                "company": company,
                "titles": titles,
                "listing_date": listing.isoformat(),
                "requested": [start.isoformat(), end.isoformat()],
                "fetched_at": datetime.now(UTC).isoformat(),
                "daily_views": dict(sorted(merged.items())),
            }, indent=1) + "\n")
            n += 1
            logger.info("%-24s %d titles, %d days of pageviews",
                        company[:24], len(titles), len(merged))
    finally:
        await client.aclose()
    return n


def build() -> "object":
    """Join underpricing to abnormal attention. No network."""
    import numpy as np
    import pandas as pd

    wl = pd.read_csv(DATA / "watchlist.csv")
    cen = pd.read_parquet(DATA / "census.parquet")
    px = pd.read_parquet(DATA / "tiingo_daily.parquet")

    cen["jd"] = cen["calendar_date"].astype(str)
    priced = cen[(cen["status"] == "priced") & cen["price_low"].notna()
                 & (cen["price_low"] == cen["price_high"])]
    # Joined on symbol AND date: 52 symbols are duplicated among priced rows
    # because SPAC shells recycle tickers, so symbol alone fans out.
    # Rows with no census match carry NaN join keys, and NaN==NaN counts as a
    # duplicate for the one-to-one validator. Dropped first so the validation
    # checks what it is meant to check -- that a symbol+date pair is unique.
    wl = wl[wl["finnhub_symbol"].notna() & wl["finnhub_date"].notna()]
    j = wl.merge(priced[["symbol", "jd", "price_low", "total_shares_value"]],
                 left_on=["finnhub_symbol", "finnhub_date"],
                 right_on=["symbol", "jd"], how="inner", validate="one_to_one")

    d1 = (px[px["session"] == 0][["company", "day", "open", "close"]]
          .rename(columns={"open": "d1_open", "close": "d1_close"}))
    j = j.merge(d1, on="company", how="inner")

    j["offer_price"] = j["price_low"]
    # The two standard forms. `underpricing` is the textbook measure; the opening
    # pop excludes the first session's own trading dynamics and is closer to
    # pure pre-market demand.
    j["underpricing"] = j["d1_close"] / j["offer_price"] - 1
    j["opening_pop"] = j["d1_open"] / j["offer_price"] - 1

    recs = []
    for _, row in j.iterrows():
        path = RAW_DAILY / f"{slug(row['company'])}.json"
        if not path.exists():
            continue
        blob = json.loads(path.read_text())
        views = {date.fromisoformat(f"{k[:4]}-{k[4:6]}-{k[6:8]}"): v
                 for k, v in (blob.get("daily_views") or {}).items()}
        listing = date.fromisoformat(blob["listing_date"])

        event = [views.get(listing - timedelta(days=d), None)
                 for d in range(1, EVENT_DAYS + 1)]
        base = [views.get(listing - timedelta(days=d), None)
                for d in range(BASELINE_END, BASELINE_START + 1)]
        event = [v for v in event if v is not None]
        base = [v for v in base if v is not None]
        if len(event) < EVENT_DAYS * 0.8 or len(base) < 30:
            # Too little coverage to form a ratio. Recorded as missing rather
            # than computed from a handful of days.
            recs.append({"company": row["company"], "event_mean": np.nan,
                         "baseline_mean": np.nan, "abnormal_attention": np.nan,
                         "event_days": len(event), "baseline_days": len(base)})
            continue
        em, bm = float(np.mean(event)), float(np.mean(base))
        recs.append({
            "company": row["company"],
            "event_mean": em,
            "baseline_mean": bm,
            # Abnormal attention: the event window relative to the company's own
            # baseline. Controls for fame, which raw views cannot.
            "abnormal_attention": em / bm if bm > 0 else np.nan,
            "event_days": len(event), "baseline_days": len(base),
        })

    att = pd.DataFrame(recs)
    out = j.merge(att, on="company", how="left")
    keep = ["company", "listing_date", "offer_price", "d1_open", "d1_close",
            "underpricing", "opening_pop", "total_shares_value", "event_mean",
            "baseline_mean", "abnormal_attention", "event_days", "baseline_days"]
    out = out[keep].sort_values("underpricing", ascending=False)
    out.to_parquet(UNDERPRICING_PARQUET, index=False)
    logger.info("underpricing: %d companies (%d with abnormal attention) -> %s",
                len(out), int(out["abnormal_attention"].notna().sum()),
                UNDERPRICING_PARQUET)
    return out


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    if not args.build_only:
        await collect()
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
