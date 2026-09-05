"""Turning EDGAR filings into rows.

Idempotency is structural, not procedural. The poller keeps no watermark and no
"already processed" bookkeeping; it re-reads a rolling window of daily indexes
every run and relies on two database constraints to absorb the overlap:

  * filings.accession_no is UNIQUE -- an accession number identifies exactly one
    submission forever, so re-reading a day inserts nothing new.
  * issuers.cik is UNIQUE -- the issuer upsert merges instead of duplicating.

That is what makes the worker safe to restart mid-run, run twice concurrently,
or leave off for a week. The cost is a handful of redundant index fetches per
run, which is cheap and buys self-healing.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import asyncpg

from backend.config import Settings
from backend.normalize import normalize_company_name
from backend.sec import index as sec_index
from backend.sec.client import SecClient
from backend.ingest.offerings import OfferingReport, extract_for_filings
from backend.sec.submissions import fetch_primary_documents, fetch_profile

logger = logging.getLogger(__name__)

# Why ingestion never infers 'priced' from a 424B4:
#
# A 424B4 is a final prospectus, but it is used for far more than IPO pricing --
# shelf takedowns, at-the-market programmes and resale prospectuses from
# companies that have traded for years all arrive as 424B4. In the first live
# run, six of seven issuers a form-type rule marked 'priced' already had Nasdaq
# tickers; one was a prospectus supplement attaching a quarterly report, whose
# only dollar figure was the previous day's closing price.
#
# So form type alone is not evidence that a deal priced. Advancing past 'filed'
# requires reading the prospectus and confirming it is an IPO -- Phase 2b. Until
# then the status stays where the registration put it. A gap beats a
# plausible-looking wrong value.

# Forms that establish a company is on file to go public. A 424B4 is NOT here,
# and that is the whole point -- see _status_for().
_REGISTRATION_FORMS = frozenset({"S-1", "S-1/A", "F-1", "F-1/A"})


@dataclass
class IngestReport:
    days_scanned: int = 0
    entries_seen: int = 0
    issuers_inserted: int = 0
    issuers_updated: int = 0
    filings_inserted: int = 0
    filings_skipped: int = 0
    profiles_missing: list[str] = field(default_factory=list)
    offerings: OfferingReport = field(default_factory=OfferingReport)

    def __str__(self) -> str:
        return (
            f"days={self.days_scanned} entries={self.entries_seen} "
            f"issuers(+{self.issuers_inserted}/~{self.issuers_updated}) "
            f"filings(+{self.filings_inserted}/skip {self.filings_skipped}) "
            f"offerings[{self.offerings}]"
        )


# Status only ever moves forward along filed -> priced -> listed. Without the
# ladder, re-reading an old S-1/A would drag a priced issuer back to 'filed'
# every time the lookback window slid over it. 'withdrawn' is set by hand and
# never overwritten by ingestion.
_UPSERT_ISSUER = """
    INSERT INTO issuers (cik, legal_name, normalized_name, sector, ticker, exchange,
                         status, first_filed_at)
    VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'filed'), $8)
    ON CONFLICT (cik) DO UPDATE SET
        legal_name      = EXCLUDED.legal_name,
        normalized_name = EXCLUDED.normalized_name,
        -- COALESCE keeps a known value rather than letting a later filing that
        -- happens to lack the field blank it out.
        sector          = COALESCE(EXCLUDED.sector, issuers.sector),
        ticker          = COALESCE(EXCLUDED.ticker, issuers.ticker),
        exchange        = COALESCE(EXCLUDED.exchange, issuers.exchange),
        status          = CASE
            WHEN EXCLUDED.status IS NULL THEN issuers.status
            WHEN issuers.status = 'withdrawn' THEN issuers.status
            WHEN array_position(ARRAY['filed','priced','listed'], EXCLUDED.status)
               > array_position(ARRAY['filed','priced','listed'], issuers.status)
            THEN EXCLUDED.status
            ELSE issuers.status
        END,
        -- LEAST ignores NULLs, so the earliest known registration wins even if
        -- we meet the company through a later amendment first.
        first_filed_at  = LEAST(issuers.first_filed_at, EXCLUDED.first_filed_at),
        updated_at      = now()
    RETURNING id, (xmax = 0) AS inserted
"""

# A filing is immutable once submitted, so a conflict means "already have it"
# and there is nothing to update. DO NOTHING returns no row, which is exactly
# how the caller distinguishes new from seen.
_INSERT_FILING = """
    INSERT INTO filings (issuer_id, cik, accession_no, form_type, filed_at, primary_doc_url)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (accession_no) DO NOTHING
    RETURNING id
"""


async def ingest_recent(
    pool: asyncpg.Pool,
    client: SecClient,
    settings: Settings,
    today: date | None = None,
) -> IngestReport:
    report = IngestReport()
    today = today or datetime.now(UTC).date()
    window_start = today - timedelta(days=settings.sec_lookback_days)

    published = await sec_index.available_days(client, today)
    # Quarter boundaries: if the window reaches back past the start of this
    # quarter, the previous quarter's listing has the rest of the days.
    if window_start < today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1):
        published += await sec_index.available_days(client, window_start)

    days = sorted({d for d in published if window_start <= d <= today})
    logger.info("edgar: %d published index days in window", len(days))

    entries: list[sec_index.IndexEntry] = []
    for day in days:
        entries.extend(await sec_index.fetch_day(client, day))
        report.days_scanned += 1

    report.entries_seen = len(entries)
    if not entries:
        return report

    # One submissions request per distinct company, not per filing.
    profiles = {}
    for cik in sorted({e.cik for e in entries}):
        profile = await fetch_profile(client, cik)
        if profile is None:
            report.profiles_missing.append(cik)
        else:
            profiles[cik] = profile

    new_filings: list[dict] = []

    async with pool.acquire() as connection:
        for entry in entries:
            profile = profiles.get(entry.cik)
            legal_name = profile.name if profile else entry.company_name
            is_registration = entry.form_type in _REGISTRATION_FORMS
            status = "filed" if is_registration else None
            # Only a registration establishes a first-filed date.
            first_filed = entry.filed_at if is_registration else None

            async with connection.transaction():
                issuer_id, inserted = await connection.fetchrow(
                    _UPSERT_ISSUER,
                    entry.cik,
                    legal_name,
                    normalize_company_name(legal_name),
                    profile.sector if profile else None,
                    profile.ticker if profile else None,
                    profile.exchange if profile else None,
                    status,
                    first_filed,
                )
                if inserted:
                    report.issuers_inserted += 1
                else:
                    report.issuers_updated += 1

                filing_id = await connection.fetchval(
                    _INSERT_FILING,
                    issuer_id,
                    entry.cik,
                    entry.accession_no,
                    entry.form_type,
                    entry.filed_at,
                    entry.primary_doc_url,
                )
                if filing_id is None:
                    report.filings_skipped += 1
                else:
                    report.filings_inserted += 1
                    # Only newly inserted filings get parsed. A prospectus does
                    # not change once filed, so re-extracting the whole lookback
                    # window each hour would download megabytes to no effect.
                    new_filings.append({
                        "id": filing_id,
                        "issuer_id": issuer_id,
                        "cik": entry.cik,
                        "accession_no": entry.accession_no,
                        "form_type": entry.form_type,
                    })

    if new_filings:
        documents: dict[str, dict[str, str]] = {}
        for cik in sorted({f["cik"] for f in new_filings}):
            documents[cik] = await fetch_primary_documents(client, cik)
        report.offerings = await extract_for_filings(
            pool, client, settings, new_filings, documents
        )

    return report
