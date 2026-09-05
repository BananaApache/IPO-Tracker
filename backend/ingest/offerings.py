"""Persisting extracted offering terms.

Runs only for filings that were *newly* inserted this pass. Prospectus documents
are 1-3 MB each, so re-extracting the whole lookback window every hour would be
both slow and pointless -- the document does not change once filed.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg

from backend.config import Settings
from backend.extract.prospectus import extract
from backend.extract.text import html_to_text
from backend.sec.client import SecClient
from backend.sec.submissions import primary_document_url

logger = logging.getLogger(__name__)

EXTRACTABLE_FORMS = frozenset({"S-1", "S-1/A", "F-1", "F-1/A", "424B4"})

# Refuse to pull an unreasonably large document into memory.
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

# Below this, nothing is written. Kept high on purpose: a wrong price at low
# confidence still ends up in a chart.
MIN_CONFIDENCE = 0.6


@dataclass
class OfferingReport:
    attempted: int = 0
    prices_written: int = 0
    not_yet_disclosed: int = 0
    not_found: int = 0
    underwriters_written: int = 0
    skipped_no_document: int = 0
    methods: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"attempted={self.attempted} price={self.prices_written} "
            f"tbd={self.not_yet_disclosed} none={self.not_found} "
            f"banks={self.underwriters_written}"
        )


_UPSERT_OFFERING = """
    INSERT INTO offerings (issuer_id, price_low, price_high, price_final,
                           source_filing_id, extraction_confidence,
                           extraction_method, extracted_at, price_disclosure)
    VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8)
    ON CONFLICT (issuer_id) DO UPDATE SET
        -- An amendment updates the deal in place. COALESCE keeps a previously
        -- extracted price when a later filing has none: an S-1/A that only
        -- restates financials should not erase a range read off the last one.
        price_low             = COALESCE(EXCLUDED.price_low, offerings.price_low),
        price_high            = COALESCE(EXCLUDED.price_high, offerings.price_high),
        price_final           = COALESCE(EXCLUDED.price_final, offerings.price_final),
        source_filing_id      = COALESCE(EXCLUDED.source_filing_id, offerings.source_filing_id),
        extraction_confidence = EXCLUDED.extraction_confidence,
        extraction_method     = EXCLUDED.extraction_method,
        extracted_at          = now(),
        -- 'disclosed' is terminal: once a real price has been read, a later
        -- document that fails to parse must not downgrade it to "not found".
        price_disclosure      = CASE
            WHEN offerings.price_disclosure = 'disclosed' THEN 'disclosed'
            ELSE EXCLUDED.price_disclosure
        END
    RETURNING id
"""

_UPSERT_UNDERWRITER = """
    INSERT INTO underwriters (name, normalized_name) VALUES ($1, $2)
    ON CONFLICT (normalized_name) DO UPDATE SET name = underwriters.name
    RETURNING id
"""

_LINK_UNDERWRITER = """
    INSERT INTO offering_underwriters (offering_id, underwriter_id, role, confidence)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (offering_id, underwriter_id) DO UPDATE SET
        role       = COALESCE(EXCLUDED.role, offering_underwriters.role),
        confidence = GREATEST(EXCLUDED.confidence, offering_underwriters.confidence)
"""


def _normalize_bank(name: str) -> str:
    return " ".join(name.lower().replace("&", " and ").split())


async def extract_for_filings(
    pool: asyncpg.Pool,
    client: SecClient,
    settings: Settings,
    filings: list[dict],
    primary_documents: dict[str, dict[str, str]],
) -> OfferingReport:
    report = OfferingReport()

    for filing in filings:
        if filing["form_type"] not in EXTRACTABLE_FORMS:
            continue
        document = primary_documents.get(filing["cik"], {}).get(filing["accession_no"])
        if not document:
            report.skipped_no_document += 1
            continue

        url = primary_document_url(filing["cik"], filing["accession_no"], document)
        try:
            response = await client.get(url)
        except Exception:
            logger.warning("offerings: could not fetch %s", url, exc_info=True)
            continue

        if len(response.content) > MAX_DOCUMENT_BYTES:
            logger.warning("offerings: %s is %d bytes, skipping", url, len(response.content))
            continue

        result = extract(html_to_text(response.text))
        report.attempted += 1
        report.methods[result.price.method] = report.methods.get(result.price.method, 0) + 1

        price = result.price
        confident = price.confidence is not None and price.confidence >= MIN_CONFIDENCE
        write_price = price.disclosure == "disclosed" and confident

        if write_price:
            report.prices_written += 1
        elif price.disclosure == "not_yet_disclosed":
            report.not_yet_disclosed += 1
        else:
            report.not_found += 1

        async with pool.acquire() as connection, connection.transaction():
            offering_id = await connection.fetchval(
                _UPSERT_OFFERING,
                filing["issuer_id"],
                price.price_low if write_price else None,
                price.price_high if write_price else None,
                price.price_final if write_price else None,
                filing["id"],
                price.confidence,
                price.method,
                price.disclosure,
            )
            for bank in result.underwriters:
                if bank.confidence < MIN_CONFIDENCE:
                    continue
                underwriter_id = await connection.fetchval(
                    _UPSERT_UNDERWRITER, bank.name, _normalize_bank(bank.name)
                )
                await connection.execute(
                    _LINK_UNDERWRITER, offering_id, underwriter_id, bank.role, bank.confidence
                )
                report.underwriters_written += 1

    return report
