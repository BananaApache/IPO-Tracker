"""Discovering and parsing EDGAR's daily index files.

Discovery is via the quarter's `index.json` rather than by constructing dates.
That matters more than it sounds: a daily index that does not exist -- a
weekend, a market holiday, a date in the future -- returns **403**, the same
status SEC uses when it is throttling you. Guessing dates therefore produces a
stream of 403s that are indistinguishable from being blocked, and a retry loop
would hammer sec.gov over files that were never going to exist. Asking which
files exist removes the ambiguity entirely.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from backend.sec.client import SecClient

# Forms that put a company on the IPO path, per the brief.
TRACKED_FORMS = frozenset({"S-1", "S-1/A", "F-1", "F-1/A", "424B4"})

_DAILY_INDEX_ROOT = "https://www.sec.gov/Archives/edgar/daily-index"

# Data rows are wider than the header claims, so fixed-width slicing silently
# truncates the date column. Match structurally instead: form, name, CIK, date,
# path.
#
# The date is matched with optional dashes because the two index families
# disagree: the quarterly full index writes 2026-09-04, the daily index writes
# 20260904. Handling only one of them parses zero rows from the other -- and
# does so silently, because an unmatched line is skipped, not raised.
_ROW = re.compile(r"^(\S+)\s+(.+?)\s{2,}(\d+)\s+(\d{4}-?\d{2}-?\d{2})\s+(\S+)\s*$")

_INDEX_NAME = re.compile(r"^form\.(\d{8})\.idx$")


@dataclass(frozen=True)
class IndexEntry:
    form_type: str
    company_name: str
    cik: str          # zero-padded to 10, EDGAR's canonical form
    filed_at: datetime  # midnight UTC of the filing date
    accession_no: str
    primary_doc_url: str


def _parse_filed_date(value: str) -> date:
    fmt = "%Y-%m-%d" if "-" in value else "%Y%m%d"
    return datetime.strptime(value, fmt).date()


def _quarter_of(day: date) -> str:
    return f"QTR{(day.month - 1) // 3 + 1}"


async def available_days(client: SecClient, day: date) -> list[date]:
    """Dates in `day`'s quarter that actually have a daily index published."""
    url = f"{_DAILY_INDEX_ROOT}/{day.year}/{_quarter_of(day)}/index.json"
    payload = await client.get_json(url)

    days: list[date] = []
    for item in payload.get("directory", {}).get("item", []):
        match = _INDEX_NAME.match(item.get("name", ""))
        if match:
            days.append(datetime.strptime(match.group(1), "%Y%m%d").date())
    return sorted(days)


async def fetch_day(client: SecClient, day: date) -> list[IndexEntry]:
    """Tracked filings from one daily index."""
    url = f"{_DAILY_INDEX_ROOT}/{day.year}/{_quarter_of(day)}/form.{day:%Y%m%d}.idx"
    return parse_index(await client.get_text(url))


def parse_index(text: str) -> list[IndexEntry]:
    entries: list[IndexEntry] = []
    for line in text.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        form_type, company_name, cik, filed, path = match.groups()
        if form_type not in TRACKED_FORMS:
            continue

        # path looks like edgar/data/1802369/0001193125-26-383838.txt
        accession_no = path.rsplit("/", 1)[-1].removesuffix(".txt")

        entries.append(
            IndexEntry(
                form_type=form_type,
                company_name=company_name.strip(),
                cik=cik.zfill(10),
                # A filing date is an SEC business date, not an instant. Pinned
                # to midnight UTC so the stored value never depends on the
                # timezone of whichever machine ran the poller.
                filed_at=datetime.combine(
                    _parse_filed_date(filed), time.min, tzinfo=UTC
                ),
                accession_no=accession_no,
                primary_doc_url=f"https://www.sec.gov/Archives/{path}",
            )
        )
    return entries
