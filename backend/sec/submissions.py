"""Company metadata from EDGAR's per-issuer submissions feed.

The daily index gives a company *name* and a CIK and nothing else. Sector,
ticker and exchange come from here.
"""

from dataclasses import dataclass

from backend.sec.client import SecClient


@dataclass(frozen=True)
class CompanyProfile:
    cik: str
    name: str
    sector: str | None
    ticker: str | None
    exchange: str | None


async def fetch_profile(client: SecClient, cik: str) -> CompanyProfile | None:
    """None when EDGAR has no submissions record for the CIK."""
    payload = await client.get_json(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
    if not payload.get("name"):
        return None

    tickers = [t for t in payload.get("tickers") or [] if t]
    exchanges = [e for e in payload.get("exchanges") or [] if e]

    return CompanyProfile(
        cik=cik.zfill(10),
        name=payload["name"],
        # SEC's own classification. Stored verbatim rather than mapped to a
        # tidier taxonomy, so the value in the database is always something a
        # reviewer can find in the source.
        sector=payload.get("sicDescription") or None,
        # Usually absent pre-IPO, which is the normal case here. Note that a
        # present value does not prove the company is trading -- some pre-IPO
        # registrants already carry an exchange record -- so ingestion never
        # infers 'listed' status from these.
        ticker=tickers[0] if tickers else None,
        exchange=exchanges[0] if exchanges else None,
    )
