"""Detecting listing events from 8-A filings.

Evidence, not absence. The earlier approach called an issuer an IPO candidate
when it had *no* ticker, which excluded 195 of the 199 real listing events in a
150-day window -- because a company acquires a ticker precisely by completing
the event being observed. An 8-A is a filing that says "these securities are
being registered on an exchange"; it is the event rather than a proxy for it.

This module fills everything except the price. `status`, `listed_on` and
`open_price` are finalised by price ingestion; until then an event sits in
`awaiting_ticker` or `registered_no_trade` and the funnel stays visible.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import asyncpg

from backend.config import Settings
from backend.sec.client import SecClient

logger = logging.getLogger(__name__)

# Reports a company files only once it is ALREADY a reporting company. A
# first-time IPO candidate has none; a post-bankruptcy re-listing has years of
# them, which is what separates AZUL (12, earliest 2018) from SPCX (0).
PERIODIC_REPORT_FORMS = frozenset({
    "10-K", "10-K/A", "10-KSB", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
})

# Registration statements. An 8-A with none of these behind it is not an
# offering -- it is an exchange registration for some other reason -- so it is
# not a listing event.
#
# Checked against the SUBMISSIONS FEED, not our own filings table. Our table
# only reaches back as far as the last backfill window, so requiring a
# registration *here* would reject a genuine IPO whose S-1 was filed before it
# and call a coverage gap a corporate fact. The submissions call is the same one
# used for periodic reports, so this costs no extra requests.
REGISTRATION_FORMS = frozenset({
    "S-1", "S-1/A", "F-1", "F-1/A", "S-11", "S-11/A", "S-4", "S-4/A",
    "424B1", "424B2", "424B3", "424B4", "424B5",
})

# After this long with a known ticker and still no trading, treat the listing as
# postponed rather than pending.
NO_TRADE_AFTER_DAYS = 30


@dataclass
class DetectionReport:
    eight_a_issuers: int = 0
    skipped_no_registration: int = 0
    inserted: int = 0
    updated: int = 0
    re_listings: int = 0
    by_status: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"8-A issuers={self.eight_a_issuers} "
            f"skipped_no_registration={self.skipped_no_registration} "
            f"events(+{self.inserted}/~{self.updated}) "
            f"re-listings={self.re_listings} status={self.by_status}"
        )


_EIGHT_A_ISSUERS = """
    SELECT f.issuer_id, i.cik, i.legal_name, i.ticker, i.exchange,
           min(f.filed_at) AS eight_a_filed_at,
           (array_agg(f.id ORDER BY f.filed_at))[1] AS eight_a_filing_id
    FROM filings f
    JOIN issuers i ON i.id = f.issuer_id
    WHERE f.form_type = ANY($1::text[])
    GROUP BY f.issuer_id, i.cik, i.legal_name, i.ticker, i.exchange
"""

# A 424B4-derived price, when extraction found one. Never required.
_IPO_PRICE = """
    SELECT o.price_final, o.source_filing_id
    FROM offerings o
    WHERE o.issuer_id = $1 AND o.price_final IS NOT NULL
    LIMIT 1
"""

_UPSERT = """
    INSERT INTO listing_events (
        issuer_id, status, eight_a_filing_id, eight_a_filed_at, ticker, exchange,
        ipo_price, ipo_price_filing_id,
        prior_periodic_reports, first_periodic_report_at, re_listing_suspected)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
    ON CONFLICT (issuer_id) DO UPDATE SET
        -- 'listed' is terminal: once trading is confirmed from a price bar,
        -- re-running detection must not walk it back to a pending state.
        status                   = CASE WHEN listing_events.status = 'listed'
                                        THEN 'listed' ELSE EXCLUDED.status END,
        eight_a_filing_id        = COALESCE(listing_events.eight_a_filing_id, EXCLUDED.eight_a_filing_id),
        eight_a_filed_at         = LEAST(listing_events.eight_a_filed_at, EXCLUDED.eight_a_filed_at),
        ticker                   = COALESCE(EXCLUDED.ticker, listing_events.ticker),
        exchange                 = COALESCE(EXCLUDED.exchange, listing_events.exchange),
        ipo_price                = COALESCE(EXCLUDED.ipo_price, listing_events.ipo_price),
        ipo_price_filing_id      = COALESCE(EXCLUDED.ipo_price_filing_id, listing_events.ipo_price_filing_id),
        prior_periodic_reports   = EXCLUDED.prior_periodic_reports,
        first_periodic_report_at = EXCLUDED.first_periodic_report_at,
        re_listing_suspected     = EXCLUDED.re_listing_suspected,
        updated_at               = now()
    RETURNING (xmax = 0) AS inserted, status
"""


@dataclass(frozen=True)
class FilingHistory:
    periodic_count: int
    periodic_earliest: date | None
    registration_count: int


async def _filing_history(client: SecClient, cik: str, before: date) -> FilingHistory:
    """What this issuer had filed before `before`, from one submissions call.

    Caveat: the submissions feed returns roughly the most recent 1,000 filings.
    A company that stopped reporting long ago and has filed heavily since could
    have its old periodic reports fall outside that window. Not currently
    detected.
    """
    payload = await client.get_json(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
    recent = payload.get("filings", {}).get("recent", {})
    cutoff = before.isoformat()

    periodic, registrations = [], 0
    for form, filed in zip(recent.get("form", []), recent.get("filingDate", []), strict=False):
        if filed >= cutoff:
            continue
        if form in PERIODIC_REPORT_FORMS:
            periodic.append(filed)
        elif form in REGISTRATION_FORMS:
            registrations += 1

    return FilingHistory(
        periodic_count=len(periodic),
        periodic_earliest=date.fromisoformat(min(periodic)) if periodic else None,
        registration_count=registrations,
    )


async def detect_events(
    pool: asyncpg.Pool, client: SecClient, settings: Settings, today: date | None = None
) -> DetectionReport:
    report = DetectionReport()
    today = today or datetime.now(UTC).date()

    from backend.sec.index import EXCHANGE_REGISTRATION_FORMS

    async with pool.acquire() as connection:
        rows = await connection.fetch(_EIGHT_A_ISSUERS, sorted(EXCHANGE_REGISTRATION_FORMS))
    report.eight_a_issuers = len(rows)

    for row in rows:
        filed_on = row["eight_a_filed_at"].date()
        history = await _filing_history(client, row["cik"], filed_on)

        # Condition (b) of the event rule. An 8-A with no registration behind it
        # is not an offering, so no event is created -- the 8-A filing itself
        # stays in `filings`, so the decision is re-derivable.
        if history.registration_count == 0:
            report.skipped_no_registration += 1
            continue

        count, earliest = history.periodic_count, history.periodic_earliest
        re_listing = count > 0

        # Price ingestion promotes to 'listed'. Until then the event sits in a
        # pending state rather than being dropped, so the funnel is countable.
        if not row["ticker"]:
            status = "awaiting_ticker"
        elif (today - filed_on).days > NO_TRADE_AFTER_DAYS:
            status = "registered_no_trade"
        else:
            status = "awaiting_ticker"

        async with pool.acquire() as connection:
            price = await connection.fetchrow(_IPO_PRICE, row["issuer_id"])
            result = await connection.fetchrow(
                _UPSERT,
                row["issuer_id"], status, row["eight_a_filing_id"], row["eight_a_filed_at"],
                row["ticker"], row["exchange"],
                price["price_final"] if price else None,
                price["source_filing_id"] if price else None,
                count, earliest, re_listing,
            )

        if result["inserted"]:
            report.inserted += 1
        else:
            report.updated += 1
        if re_listing:
            report.re_listings += 1
        report.by_status[result["status"]] = report.by_status.get(result["status"], 0) + 1

    return report
