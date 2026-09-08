"""Tier B: turn the seed CSV into an enriched, verified watchlist.

The seed CSV carries a brand name, a ticker, and a month. None of those are
enough to collect against: news coverage uses brand names that differ from the
registrant ("Instacart" files as "Maplebear Inc."), and the Year/Month columns
are month-granularity and unverified. This script resolves each row against
EDGAR and Finnhub and writes `data/watchlist.csv`.

What it does NOT do is overwrite the seed. Where EDGAR and the CSV disagree, the
EDGAR value goes in its own column and `date_flag` records the disagreement, so
a wrong seed value is visible rather than laundered.

Run:  uv run --group research python -m research.collect.edgar_enrich
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from backend.config import Settings
from backend.sec.client import SecClient
from research.collect.aliases import query_for
from research.collect.paths import (
    CENSUS_PARQUET,
    RAW_EDGAR,
    SEED_CSV,
    WATCHLIST_CSV,
    ensure_dirs,
)

logger = logging.getLogger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# The registration form that starts a US IPO, in the order we prefer to believe
# it. S-1 for a domestic issuer, F-1 for a foreign private issuer. S-4 is the
# SPAC-merger path and is NOT an IPO registration -- it is recorded when found
# so the row can be flagged, not treated as equivalent.
IPO_FORMS = ("S-1", "F-1")
SPAC_FORMS = ("S-4",)
# Exchange registration. This is the filing that actually accompanies a listing,
# which is why the deployed pipeline uses it for listing detection too.
LISTING_FORMS = ("8-A12B", "8-A12G", "8-A12B/A", "8-A12G/A")

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


@dataclass
class Row:
    # --- from the seed CSV, never overwritten ---
    company: str
    seed_ticker: str
    seed_year: int | None
    seed_month: int | None
    seed_exchange: str
    method: str
    sector: str
    notes: str
    # --- frozen query form, set before any counting ---
    nyt_query: str = ""
    twitter_query: str = ""
    ambiguous: bool = False
    query_note: str = ""
    # --- resolved from EDGAR ---
    cik: str | None = None
    legal_name: str | None = None
    sic_description: str | None = None
    edgar_ticker: str | None = None
    edgar_exchange: str | None = None
    resolution_method: str = ""
    registration_form: str | None = None
    registration_filed_at: str | None = None
    listing_form: str | None = None
    listing_filed_at: str | None = None
    # --- joined from the Tier A census ---
    finnhub_name: str | None = None
    finnhub_symbol: str | None = None
    finnhub_status: str | None = None
    finnhub_date: str | None = None
    finnhub_match: str = "unmatched"
    # --- derived listing anchor ---
    listing_date: str | None = None
    listing_date_source: str = ""
    price_available: bool = False
    # --- verdicts ---
    date_flag: str = ""
    tier_b: bool = True
    tier_b_priority: int = 9
    exclude_reason: str = ""
    aliases: str = ""


# Columns whose values must stay text. A CIK is a zero-padded identifier, not a
# number: pandas infers `0001769628` as the float 1769628.0, which drops the
# padding and would silently fail to match EDGAR. Tickers are text for the same
# reason -- a symbol like "TRUE" or one that looks numeric must not be coerced.
WATCHLIST_STR_COLUMNS = (
    "cik", "seed_ticker", "edgar_ticker", "finnhub_symbol", "registration_filed_at",
    "listing_filed_at", "listing_date", "finnhub_date",
)


def read_watchlist_df():
    """watchlist.csv as a DataFrame, with identifier columns kept as text.

    The one supported way to read this file with pandas. `pd.read_csv` on its
    own gets the CIK wrong, and getting it wrong is invisible until a lookup
    misses.
    """
    import pandas as pd

    return pd.read_csv(
        WATCHLIST_CSV,
        dtype={c: "string" for c in WATCHLIST_STR_COLUMNS},
        keep_default_na=True,
    )


def read_seed() -> list[Row]:
    rows: list[Row] = []
    with SEED_CSV.open(newline="") as fh:
        for raw in csv.DictReader(fh):
            company = (raw["Company"] or "").strip()
            # "Nu Holdings (Nubank)" and "Instacart (Maplebear)" carry the
            # alternate name in parentheses; both halves are real aliases.
            brand = company.split("(")[0].strip()
            paren = company[company.find("(") + 1:company.rfind(")")].strip() \
                if "(" in company and ")" in company else ""
            nyt_q, tw_q, why, amb = query_for(brand)
            rows.append(Row(
                company=company,
                seed_ticker=(raw["Ticker"] or "").strip().upper(),
                seed_year=int(raw["Year"]) if (raw["Year"] or "").strip() else None,
                seed_month=MONTHS.get((raw["Month"] or "").strip()),
                seed_exchange=(raw["Exchange"] or "").strip(),
                method=(raw["Method"] or "").strip(),
                sector=(raw["Sector"] or "").strip(),
                notes=(raw["Notes"] or "").strip(),
                nyt_query=nyt_q, twitter_query=tw_q, ambiguous=amb, query_note=why,
                aliases="|".join(x for x in (brand, paren) if x),
            ))
    return rows


async def _cached_json(client: SecClient, url: str, cache_name: str,
                       params: dict[str, Any] | None = None) -> Any:
    """GET with an on-disk cache. EDGAR is polite but not free, and rerunning
    the enrichment while iterating on it should not re-hit sec.gov."""
    path = RAW_EDGAR / cache_name
    if path.exists():
        return json.loads(path.read_text())
    payload = await client.get_json(url, params=params)
    path.write_text(json.dumps(payload, indent=1) + "\n")
    return payload


async def resolve_cik(client: SecClient, row: Row, ticker_map: dict[str, dict]) -> None:
    """Fill cik/legal_name. Ticker first, then EDGAR full-text search by name."""
    if row.seed_ticker and row.seed_ticker in ticker_map:
        hit = ticker_map[row.seed_ticker]
        row.cik = str(hit["cik_str"]).zfill(10)
        row.legal_name = hit["title"]
        row.resolution_method = "ticker in company_tickers.json"
        return

    # No ticker, or a ticker EDGAR does not carry (foreign listing, or a symbol
    # from the seed that never existed). Fall back to full-text search over
    # filings, which matches on the registrant name EDGAR itself uses.
    brand = row.company.split("(")[0].strip()
    try:
        payload = await _cached_json(
            client, FTS_URL,
            f"fts_{brand.lower().replace(' ', '_').replace('/', '_')}.json",
            params={"q": f'"{brand}"', "forms": "S-1,F-1,S-4"},
        )
    except Exception as exc:
        row.resolution_method = f"full-text search failed: {type(exc).__name__}"
        return

    entities: dict[str, str] = {}
    for hit in ((payload.get("hits") or {}).get("hits") or []):
        src = hit.get("_source") or {}
        for name_cik in src.get("display_names") or []:
            # display_names look like "Maplebear Inc. (CART) (CIK 0001579091)"
            if "(CIK " in name_cik:
                cik = name_cik.rsplit("(CIK ", 1)[1].rstrip(")").strip()
                entities[cik] = name_cik.split(" (")[0]
    if len(entities) == 1:
        cik, name = next(iter(entities.items()))
        row.cik = cik.zfill(10)
        row.legal_name = name
        row.resolution_method = "EDGAR full-text search, single registrant"
    elif entities:
        # Ambiguous on purpose rather than guessed. The first hit is not
        # reliably the right registrant, and a wrong CIK poisons every date
        # downstream.
        row.resolution_method = (
            f"EDGAR full-text search returned {len(entities)} registrants: "
            + "; ".join(list(entities.values())[:4])
        )
    else:
        row.resolution_method = "EDGAR full-text search returned no registrant"


async def _all_filings(client: SecClient, cik: str) -> list[tuple[str, str]]:
    """(form, filing_date) for every filing EDGAR holds for a CIK.

    `filings.recent` holds only the most recent ~1000 filings. A 2019 issuer
    that has filed steadily since can easily have pushed its own S-1 out of that
    window, so the older shards in `filings.files` are read too -- otherwise the
    S-1 date comes back null for exactly the oldest and most interesting rows.
    """
    payload = await _cached_json(client, SUBMISSIONS_URL.format(cik=cik),
                                 f"submissions_{cik}.json")
    out: list[tuple[str, str]] = []
    recent = (payload.get("filings") or {}).get("recent") or {}
    out.extend(zip(recent.get("form") or [], recent.get("filingDate") or []))

    for shard in ((payload.get("filings") or {}).get("files") or []):
        name = shard.get("name")
        if not name:
            continue
        extra = await _cached_json(
            client, f"https://data.sec.gov/submissions/{name}", f"submissions_{name}")
        out.extend(zip(extra.get("form") or [], extra.get("filingDate") or []))
    return out


async def enrich_from_edgar(client: SecClient, row: Row) -> None:
    if not row.cik:
        return
    profile = await _cached_json(client, SUBMISSIONS_URL.format(cik=row.cik),
                                f"submissions_{row.cik}.json")
    row.legal_name = profile.get("name") or row.legal_name
    row.sic_description = profile.get("sicDescription") or None
    tickers = [t for t in profile.get("tickers") or [] if t]
    exchanges = [e for e in profile.get("exchanges") or [] if e]
    row.edgar_ticker = tickers[0] if tickers else None
    row.edgar_exchange = exchanges[0] if exchanges else None

    filings = await _all_filings(client, row.cik)
    # Earliest filing of each kind. "First S-1" is the registration event the
    # module measures against; a later S-1/A is an amendment to it.
    def earliest(forms: tuple[str, ...]) -> tuple[str | None, str | None]:
        hits = sorted((d, f) for f, d in filings if f in forms and d)
        return (hits[0][1], hits[0][0]) if hits else (None, None)

    form, filed = earliest(IPO_FORMS)
    if not form:
        form, filed = earliest(SPAC_FORMS)
    row.registration_form, row.registration_filed_at = form, filed
    row.listing_form, row.listing_filed_at = earliest(LISTING_FORMS)


def join_census(rows: list[Row]) -> None:
    """Join each watchlist row to its Tier A record, or record why it did not."""
    import pandas as pd

    if not CENSUS_PARQUET.exists():
        logger.warning("no census parquet yet; skipping the Tier A join")
        return
    census = pd.read_parquet(CENSUS_PARQUET)
    by_symbol: dict[str, list[dict]] = {}
    for rec in census.to_dict("records"):
        if rec.get("symbol"):
            by_symbol.setdefault(rec["symbol"], []).append(rec)
    names = {str(rec["name"]).casefold(): rec for rec in census.to_dict("records")}

    for row in rows:
        cands = by_symbol.get(row.edgar_ticker or row.seed_ticker or "", [])
        how = "symbol"
        if not cands:
            for key in (row.legal_name, row.company.split("(")[0].strip()):
                if key and key.casefold() in names:
                    cands, how = [names[key.casefold()]], "legal name"
                    break
        if not cands:
            row.finnhub_match = "unmatched"
            continue
        # Prefer a priced row: that is the one carrying the real deal terms.
        best = sorted(cands, key=lambda r: (r.get("status") != "priced",
                                            str(r.get("calendar_date"))))[0]
        row.finnhub_name = best.get("name")
        row.finnhub_symbol = best.get("symbol")
        row.finnhub_status = best.get("status")
        row.finnhub_date = str(best.get("calendar_date"))
        row.finnhub_match = f"matched on {how}"


def apply_verdicts(rows: list[Row], *, price_floor: date) -> None:
    """Derive the listing anchor, flag date disagreements, rank Tier B.

    The listing date is NOT the 8-A filing date. An 8-A registers a class of
    securities and is filed before trading starts -- sometimes long before, and
    sometimes for a listing that then changes shape. Roblox filed its 8-A on
    2020-12-03 and first traded on 2021-03-10 after switching from an IPO to a
    direct listing; Coinbase's 8-A precedes its first trade by three weeks. So
    the 8-A is used as a lower bound and corroboration, never as the anchor.

    Preference order, best evidence first:
      1. the Finnhub census date on a `priced` row -- closest thing available
         here to the actual first trading day
      2. the Finnhub census date on any status, flagged as provisional
      3. the 8-A date, flagged explicitly as a lower bound

    The module brief asks for the actual first trade to win over the calendar.
    That reconciliation needs a price series, and the licensed feed only reaches
    back two years (see data/source_probe.json), so it can only be done for the
    recent rows -- collect/prices.py does it there and writes the correction back.
    """
    for row in rows:
        if row.finnhub_date and row.finnhub_date != "None":
            row.listing_date = row.finnhub_date
            row.listing_date_source = (
                "Finnhub calendar, priced" if row.finnhub_status == "priced"
                else f"Finnhub calendar, status={row.finnhub_status} (provisional)")
        elif row.listing_filed_at:
            row.listing_date = row.listing_filed_at
            row.listing_date_source = "8-A filing date (LOWER BOUND, not first trade)"
        else:
            row.listing_date_source = "none found"

        # --- date cross-check against the unverified seed month ---
        if row.listing_date and row.seed_year and row.seed_month:
            try:
                got = date.fromisoformat(str(row.listing_date)[:10])
            except ValueError:
                got = None
            if got:
                agrees = (got.year, got.month) == (row.seed_year, row.seed_month)
                row.date_flag = (
                    "seed month agrees with evidence" if agrees else
                    f"seed says {row.seed_year}-{row.seed_month:02d}, evidence says "
                    f"{got.isoformat()} via {row.listing_date_source}")
                # An 8-A after the listing date would mean the anchor is wrong.
                if row.listing_filed_at and got and str(row.listing_filed_at) > str(got):
                    row.date_flag += f"; NOTE 8-A ({row.listing_filed_at}) postdates it"
        elif not row.listing_date:
            row.date_flag = "no listing evidence found; seed month unverified"

        row.price_available = bool(
            row.listing_date and str(row.listing_date)[:10] >= price_floor.isoformat())

        # --- Tier B inclusion, in priority order, one reason each ---
        non_us = row.seed_exchange in ("Frankfurt", "Shanghai STAR")
        if non_us:
            row.tier_b = False
            row.exclude_reason = f"non-US listing ({row.seed_exchange}); not in the Finnhub US census"
        elif not row.cik:
            row.tier_b = False
            row.exclude_reason = f"no CIK resolved -- {row.resolution_method}"
        elif row.method == "SPAC Merger":
            row.tier_b = False
            row.exclude_reason = "SPAC merger: registers on S-4/proxy, so there is no S-1 anchor"
        elif not row.registration_filed_at:
            row.tier_b = False
            row.exclude_reason = "no S-1/F-1 found in EDGAR; nothing to anchor the pre-filing window on"

        # Collection order, fixed here and not revisited after seeing counts.
        # Rank 1 is a row that can appear in BOTH halves of the analysis --
        # attention trajectory AND attention-versus-return -- because the
        # licensed price feed covers its listing. Collecting those first means a
        # quota-limited run yields whole companies rather than fragments of all
        # of them, and yields the ones that can actually answer the question.
        if not row.tier_b:
            row.tier_b_priority = 9
        elif row.price_available:
            row.tier_b_priority = 1
        else:
            row.tier_b_priority = 2


def write_watchlist(rows: list[Row]) -> None:
    rows = sorted(rows, key=lambda r: (r.tier_b_priority, str(r.listing_date or "9999")))
    fields = list(asdict(rows[0]).keys())
    with WATCHLIST_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row))
    logger.info("wrote %s (%d rows, %d in Tier B)", WATCHLIST_CSV, len(rows),
                sum(1 for r in rows if r.tier_b))


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="process only the first N rows")
    # The licensed price feed grants a rolling two-year window; measured at
    # exactly 730 days on 2026-09-08. Passed in rather than computed so a rerun
    # reproduces an earlier watchlist instead of silently reclassifying rows as
    # the window slides.
    ap.add_argument("--price-floor", type=lambda v: date.fromisoformat(v),
                    default=datetime.now(UTC).date() - timedelta(days=730),
                    help="earliest date the price feed covers (default: today - 730d)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    ensure_dirs()

    rows = read_seed()
    if args.limit:
        rows = rows[: args.limit]
    logger.info("seed: %d rows", len(rows))

    client = SecClient(Settings())
    try:
        raw_map = await _cached_json(client, TICKER_MAP_URL, "company_tickers.json")
        ticker_map = {str(v["ticker"]).upper(): v for v in raw_map.values()}
        logger.info("company_tickers.json: %d tickers", len(ticker_map))

        for row in rows:
            await resolve_cik(client, row, ticker_map)
            await enrich_from_edgar(client, row)
            logger.info("%-28s cik=%-10s S-1=%-10s 8-A=%-10s %s",
                        row.company[:28], row.cik or "-",
                        row.registration_filed_at or "-", row.listing_filed_at or "-",
                        row.resolution_method[:44])
    finally:
        await client.aclose()

    join_census(rows)
    apply_verdicts(rows, price_floor=args.price_floor)
    write_watchlist(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
