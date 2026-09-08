"""The pre-listing event timeline, from EDGAR data already on disk.

This module adds no new provider. Every filing it reads was already downloaded
by `edgar_enrich`, which used it only for two dates and threw the rest away. The
rest turns out to be the most informative material in the module.

**DRS is the headline.** Under the JOBS Act an emerging growth company files a
*confidential* draft registration statement before its public S-1, and EDGAR
exposes it only once the company actually goes public. Measured across the
watchlist: **every company that filed an S-1 also filed a DRS first**, with a
median lead of ~105 days and a maximum over 1,400.

Two things follow.

1. **The public S-1 is a leaky event anchor.** "Attention before the S-1"
   silently includes however long the company had already been in registration
   -- 798 days for Reddit. For the longest-gap companies the DRS falls outside
   the module's 24-month window entirely.
2. **It supports a sharper question than the module currently asks.** The DRS
   was secret when filed; the S-1 was public. So: does attention rise around the
   confidential filing or only the public one? Attention that rises after the
   DRS but before the S-1 is information leakage -- bankers, employees,
   investors -- and that is falsifiable with what is already collected.

Note the asymmetry, because it matters for how this is described: DRS is useless
to the deployed pipeline, which detects issuers in real time and cannot see a
filing that is not public yet. It is available only retrospectively, which is
exactly what a retrospective module needs.

Also extracted, all free and all previously unused:

* **Form D** -- private placements. A pre-IPO funding timeline with dates.
* **CORRESP / UPLOAD** -- SEC comment letters, in both directions. The number of
  rounds is a measure of how much friction the registration met.
* **RW / AW** -- actual registration withdrawal, which is what makes a
  "withdrawn" comparison group evidence rather than a Finnhub status string.
* **S-1/A and DRS/A counts** -- amendment cadence, another friction measure.

Run:  uv run --group research python -m research.collect.edgar_events
      uv run --group research python -m research.collect.edgar_events --offline
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

from backend.config import Settings
from backend.sec.client import SecClient
from research.collect.edgar_enrich import _all_filings, read_watchlist_df
from research.collect.paths import DATA, RAW_EDGAR, ensure_dirs

logger = logging.getLogger(__name__)

# Confidential draft registration, its amendments, and the SEC's letters on it.
DRS_FORMS = ("DRS",)
DRS_AMEND = ("DRS/A",)
DRS_LETTERS = ("DRSLTR",)
# Public registration.
S1_FORMS = ("S-1", "F-1")
S1_AMEND = ("S-1/A", "F-1/A")
# Final prospectus -- the pricing document.
PRICING_FORMS = ("424B1", "424B4", "424B3")
# Exchange registration. Precedes the first trade, sometimes by months.
LISTING_FORMS = ("8-A12B", "8-A12G")
# Private placements.
FORM_D = ("D",)
FORM_D_AMEND = ("D/A",)
# SEC comment letters. CORRESP is the company writing in, UPLOAD is the SEC
# writing out; counting both gives the number of review rounds.
CORRESP = ("CORRESP",)
UPLOAD = ("UPLOAD",)
# Withdrawal. RW withdraws a registration statement, AW an amendment.
WITHDRAW_FORMS = ("RW", "AW", "RW WD")

EVENTS_PARQUET = DATA / "edgar_events.parquet"
FILINGS_PARQUET = DATA / "edgar_filings.parquet"


@dataclass
class Timeline:
    company: str
    cik: str
    legal_name: str | None = None

    # --- confidential phase ---
    drs_first: str | None = None
    drs_count: int = 0
    drs_amendments: int = 0
    drs_letters: int = 0

    # --- public phase ---
    s1_first: str | None = None
    s1_form: str | None = None
    s1_amendments: int = 0
    pricing_first: str | None = None
    listing_8a: str | None = None

    # --- derived ---
    drs_to_s1_days: int | None = None
    s1_to_pricing_days: int | None = None

    # --- funding ---
    form_d_count: int = 0
    form_d_first: str | None = None
    form_d_last: str | None = None
    form_d_dates: str = ""          # pipe-delimited, so the CSV stays flat

    # --- registration friction ---
    corresp_count: int = 0
    upload_count: int = 0
    comment_rounds: int = 0         # UPLOAD letters: rounds the SEC initiated

    # --- withdrawal ---
    withdrawn_at: str | None = None
    withdraw_form: str | None = None

    notes: list[str] = field(default_factory=list)


def _earliest(filings: list[tuple[str, str]], forms: tuple[str, ...]) -> tuple[str | None, str | None]:
    hits = sorted((d, f) for f, d in filings if f in forms and d)
    return (hits[0][0], hits[0][1]) if hits else (None, None)


def _all_dates(filings: list[tuple[str, str]], forms: tuple[str, ...]) -> list[str]:
    return sorted(d for f, d in filings if f in forms and d)


def _gap(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except ValueError:
        return None


def build_timeline(company: str, cik: str, legal_name: str | None,
                   filings: list[tuple[str, str]]) -> Timeline:
    t = Timeline(company=company, cik=cik, legal_name=legal_name)

    t.drs_first, _ = _earliest(filings, DRS_FORMS)
    t.drs_count = len(_all_dates(filings, DRS_FORMS))
    t.drs_amendments = len(_all_dates(filings, DRS_AMEND))
    t.drs_letters = len(_all_dates(filings, DRS_LETTERS))

    t.s1_first, t.s1_form = _earliest(filings, S1_FORMS)
    t.s1_amendments = len(_all_dates(filings, S1_AMEND))
    t.pricing_first, _ = _earliest(filings, PRICING_FORMS)
    t.listing_8a, _ = _earliest(filings, LISTING_FORMS)

    t.drs_to_s1_days = _gap(t.drs_first, t.s1_first)
    t.s1_to_pricing_days = _gap(t.s1_first, t.pricing_first)

    d_dates = _all_dates(filings, FORM_D)
    t.form_d_count = len(d_dates)
    t.form_d_first = d_dates[0] if d_dates else None
    t.form_d_last = d_dates[-1] if d_dates else None
    t.form_d_dates = "|".join(d_dates)

    t.corresp_count = len(_all_dates(filings, CORRESP))
    t.upload_count = len(_all_dates(filings, UPLOAD))
    t.comment_rounds = t.upload_count

    t.withdrawn_at, t.withdraw_form = _earliest(filings, WITHDRAW_FORMS)

    # Findings recorded as data, not left for a reader to notice.
    if t.s1_first and not t.drs_first:
        t.notes.append("public S-1 with no DRS: not an emerging growth company, "
                       "or filed before confidential review was available")
    if t.drs_to_s1_days is not None and t.drs_to_s1_days > 365:
        t.notes.append(f"DRS predates the S-1 by {t.drs_to_s1_days} days -- the "
                       f"24-month pre-S-1 window does not reach it")
    if t.listing_8a and t.pricing_first and t.listing_8a < t.pricing_first:
        t.notes.append("8-A precedes the final prospectus, as expected; it is a "
                       "lower bound on the first trade, not the listing date")
    if not t.form_d_count:
        t.notes.append("no Form D: foreign private issuer, or raised outside "
                       "Reg D exemptions")
    return t


async def collect(*, offline: bool = False) -> list[Timeline]:
    ensure_dirs()
    wl = read_watchlist_df()
    rows = wl[wl["cik"].notna()].to_dict("records")
    logger.info("edgar_events: %d companies with a resolved CIK", len(rows))

    client = None if offline else SecClient(Settings())
    timelines: list[Timeline] = []
    try:
        for row in rows:
            cik = str(row["cik"])
            cached = RAW_EDGAR / f"submissions_{cik}.json"
            if offline and not cached.exists():
                logger.warning("%s: no cached submissions and --offline; skipped",
                               row["company"])
                continue
            filings = await _all_filings(client, cik)
            t = build_timeline(row["company"], cik, row.get("legal_name"), filings)
            timelines.append(t)
            logger.info("%-26s DRS=%-10s S-1=%-10s gap=%-5s D=%-3d comments=%-3d",
                        t.company[:26], t.drs_first or "-", t.s1_first or "-",
                        t.drs_to_s1_days if t.drs_to_s1_days is not None else "-",
                        t.form_d_count, t.comment_rounds)
    finally:
        if client is not None:
            await client.aclose()
    return timelines


def write_outputs(timelines: list[Timeline]) -> None:
    import pandas as pd

    df = pd.DataFrame([{k: v for k, v in asdict(t).items() if k != "notes"}
                       | {"notes": " ; ".join(t.notes)} for t in timelines])
    for col in ("drs_first", "s1_first", "pricing_first", "listing_8a",
                "form_d_first", "form_d_last", "withdrawn_at"):
        df[col] = df[col].astype("string")
    df = df.sort_values("company").reset_index(drop=True)
    df.to_parquet(EVENTS_PARQUET, index=False)

    # Long form: one row per interesting filing, for plotting event rugs.
    long_rows = []
    for t in timelines:
        for label, value in (("drs", t.drs_first), ("s1", t.s1_first),
                             ("pricing", t.pricing_first), ("8-A", t.listing_8a),
                             ("withdrawn", t.withdrawn_at)):
            if value:
                long_rows.append({"company": t.company, "event": label, "date": value})
        for d in (t.form_d_dates.split("|") if t.form_d_dates else []):
            long_rows.append({"company": t.company, "event": "form_d", "date": d})
    long = pd.DataFrame(long_rows)
    if not long.empty:
        long["date"] = pd.to_datetime(long["date"], errors="coerce")
        long = long.dropna(subset=["date"]).sort_values(["company", "date"])
    long.to_parquet(FILINGS_PARQUET, index=False)

    gaps = df["drs_to_s1_days"].dropna()
    logger.info("edgar_events: %d companies -> %s", len(df), EVENTS_PARQUET)
    if len(gaps):
        logger.info("edgar_events: DRS in %d of %d; DRS->S-1 median %.0f days, "
                    "max %.0f", int(df["drs_first"].notna().sum()), len(df),
                    gaps.median(), gaps.max())


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="use only cached submissions; make no request to sec.gov")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    timelines = await collect(offline=args.offline)
    if not timelines:
        raise SystemExit("no timelines built")
    write_outputs(timelines)
    print(f"edgar_events: {len(timelines)} companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
