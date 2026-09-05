"""Loads a starter set of real SEC registrants into `issuers`.

Run it with:  uv run python -m backend.seed

PROVENANCE -- every field below came from SEC EDGAR on 2026-09-05, not from a
model and not from memory. Nothing here is invented:

  legal_name, cik   https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/form.idx
  sector            `sicDescription` from
                    https://data.sec.gov/submissions/CIK{cik}.json
  first_filed_at    earliest S-1 or F-1 filingDate in that same submissions feed

Selection rule: companies whose *first* registration statement is recent, which
is what makes them pre-IPO. Blank-check SPACs (SIC 6770) are excluded because
they have no operations to score, and already-listed companies filing resale
registrations are excluded because they are not IPO candidates at all.

`ticker` and `exchange` are deliberately NULL. A company that has only filed an
S-1 has no ticker yet -- that absence is the premise of this project -- and the
exchange it *intends* to list on lives in the prospectus cover text, which the
Phase 2 EDGAR worker parses. Guessing either one here is exactly the fabricated
data that would end up in a screenshot.

Phase 2 replaces this script with real ingestion. Until then it is the only way
rows get into `issuers`.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

import asyncpg

from backend.config import get_settings
from backend.normalize import normalize_company_name


@dataclass(frozen=True)
class Seed:
    cik: str
    legal_name: str
    sector: str | None
    first_filed_at: date


SEED_ISSUERS: list[Seed] = [
    Seed(
        cik='0001802369',
        legal_name='ADARx Pharmaceuticals, Inc.',
        sector='Pharmaceutical Preparations',
        first_filed_at=date(2026, 9, 4),
    ),
    Seed(
        cik='0002133022',
        legal_name='Oura Inc.',
        sector=None,
        first_filed_at=date(2026, 9, 3),
    ),
    Seed(
        cik='0002133037',
        legal_name='SB Energy, Inc.',
        sector='Electric Services',
        first_filed_at=date(2026, 9, 1),
    ),
    Seed(
        cik='0002125056',
        legal_name='Wella Co',
        sector='Perfumes, Cosmetics & Other Toilet Preparations',
        first_filed_at=date(2026, 8, 31),
    ),
    Seed(
        cik='0002141406',
        legal_name='Accelevation Holdings Corp.',
        sector='Electrical Industrial Apparatus',
        first_filed_at=date(2026, 9, 2),
    ),
    Seed(
        cik='0002080577',
        legal_name='LiPower New Energy Holdings Ltd',
        sector='Miscellaneous Electrical Machinery, Equipment & Supplies',
        first_filed_at=date(2026, 9, 2),
    ),
    Seed(
        cik='0002088082',
        legal_name='Electra Therapeutics, Inc.',
        sector='Biological Products, (No Diagnostic Substances)',
        first_filed_at=date(2026, 8, 28),
    ),
    Seed(
        cik='0002125355',
        legal_name='Bamboo Insurance Services, Inc.',
        sector='Insurance Agents, Brokers & Service',
        first_filed_at=date(2026, 8, 28),
    ),
    Seed(
        cik='0002141616',
        legal_name='Amaero Inc.',
        sector='Miscellaneous Primary Metal Products',
        first_filed_at=date(2026, 8, 28),
    ),
    Seed(
        cik='0002141512',
        legal_name='Spinnova Plc',
        sector='Miscellaneous Fabricated Textile Products',
        first_filed_at=date(2026, 8, 27),
    ),
]

# ON CONFLICT on the natural key, so re-running updates in place instead of
# either duplicating or failing. Phase 2's EDGAR upsert uses this same shape.
_UPSERT = """
    INSERT INTO issuers (cik, legal_name, normalized_name, sector, status, first_filed_at)
    VALUES ($1, $2, $3, $4, 'filed', $5)
    ON CONFLICT (cik) DO UPDATE
        SET legal_name      = EXCLUDED.legal_name,
            normalized_name = EXCLUDED.normalized_name,
            sector          = EXCLUDED.sector,
            first_filed_at  = EXCLUDED.first_filed_at,
            updated_at      = now()
    RETURNING (xmax = 0) AS inserted
"""


async def run() -> None:
    settings = get_settings()
    connection = await asyncpg.connect(settings.database_dsn, statement_cache_size=0)
    try:
        inserted = updated = 0
        # One transaction: the seed set is meaningful as a set, so a failure
        # partway through should leave no half-loaded cohort behind.
        async with connection.transaction():
            for seed in SEED_ISSUERS:
                # A filing date is an SEC business date, not an instant. Pin
                # it to midnight UTC explicitly: handing asyncpg a bare `date`
                # for a timestamptz column resolves it against the *client's*
                # local zone, so the same seed run from a machine east of UTC
                # would store the previous day.
                filed_at = datetime.combine(seed.first_filed_at, time.min, tzinfo=UTC)
                was_insert = await connection.fetchval(
                    _UPSERT,
                    seed.cik,
                    seed.legal_name,
                    normalize_company_name(seed.legal_name),
                    seed.sector,
                    filed_at,
                )
                if was_insert:
                    inserted += 1
                else:
                    updated += 1
        print(f"seed: {inserted} inserted, {updated} updated, {len(SEED_ISSUERS)} total")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run())
