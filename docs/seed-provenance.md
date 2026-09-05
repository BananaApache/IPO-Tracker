# Seed data provenance

The ten rows loaded by `backend/seed.py` are real SEC registrants. Every
field traces to a specific document; none of it was written from memory.

Pulled **2026-09-05** from:

| Field | Source |
|---|---|
| `legal_name`, `cik` | [EDGAR full index, 2026 QTR3](https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/form.idx) |
| `sector` | `sicDescription` from `https://data.sec.gov/submissions/CIK{cik}.json` |
| `first_filed_at` | earliest `S-1`/`F-1` `filingDate` in that same submissions feed |
| `ticker`, `exchange` | **not set** — see below |

## Selection rule

Companies whose *first* registration statement is recent, which is what makes
them pre-IPO. Excluded: blank-check SPACs (SIC 6770), which have no operations
to score; and already-listed companies filing resale registrations, which are
not IPO candidates.

Of 242 distinct S-1/F-1 filers in 2026 QTR3, these ten survived that filter.

## Why `ticker` and `exchange` are NULL

A company that has only filed an S-1 has no ticker — that absence is the
premise of this project. The exchange it *intends* to list on appears in the
prospectus cover text, which the Phase 2 worker parses. Filling either in by
hand would be inventing data, and invented data ends up in screenshots.

## The filings

Each row below links to the actual registration statement. All ten were
verified to return HTTP 200 on 2026-09-05.

| Company | CIK | Form | Filed | Accession | Document |
|---|---|---|---|---|---|
| ADARx Pharmaceuticals, Inc. | `0001802369` | S-1 | 2026-09-04 | `0001193125-26-383838` | [open](https://www.sec.gov/Archives/edgar/data/1802369/000119312526383838/d903461ds1.htm) |
| Oura Inc. | `0002133022` | S-1 | 2026-09-03 | `0001193125-26-381855` | [open](https://www.sec.gov/Archives/edgar/data/2133022/000119312526381855/d119865ds1.htm) |
| Accelevation Holdings Corp. | `0002141406` | S-1 | 2026-09-02 | `0001628280-26-060083` | [open](https://www.sec.gov/Archives/edgar/data/2141406/000162828026060083/accelevationllc-sx1publicf.htm) |
| LiPower New Energy Holdings Ltd | `0002080577` | F-1 | 2026-09-02 | `0001213900-26-096406` | [open](https://www.sec.gov/Archives/edgar/data/2080577/000121390026096406/ea0304333-01.htm) |
| SB Energy, Inc. | `0002133037` | S-1 | 2026-09-01 | `0001628280-26-059639` | [open](https://www.sec.gov/Archives/edgar/data/2133037/000162828026059639/sbenergy-sx1.htm) |
| Wella Co | `0002125056` | S-1 | 2026-08-31 | `0001628280-26-059572` | [open](https://www.sec.gov/Archives/edgar/data/2125056/000162828026059572/wellaoperations-sx1.htm) |
| Electra Therapeutics, Inc. | `0002088082` | S-1 | 2026-08-28 | `0001193125-26-374259` | [open](https://www.sec.gov/Archives/edgar/data/2088082/000119312526374259/d61940ds1.htm) |
| Bamboo Insurance Services, Inc. | `0002125355` | S-1 | 2026-08-28 | `0001628280-26-059433` | [open](https://www.sec.gov/Archives/edgar/data/2125355/000162828026059433/bambooinsuranceservicesinc.htm) |
| Amaero Inc. | `0002141616` | S-1 | 2026-08-28 | `0001193125-26-372368` | [open](https://www.sec.gov/Archives/edgar/data/2141616/000119312526372368/ck0002141616-20260828.htm) |
| Spinnova Plc | `0002141512` | F-1 | 2026-08-27 | `0001185185-26-003732` | [open](https://www.sec.gov/Archives/edgar/data/2141512/000118518526003732/spinnovaf1081826.htm) |

## Reproducing this

```bash
curl -A "$SEC_USER_AGENT" \
  https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/form.idx
curl -A "$SEC_USER_AGENT" \
  https://data.sec.gov/submissions/CIK0001802369.json
```

EDGAR returns `403` to any request whose `User-Agent` lacks a real contact
address, and the failure page is HTML that looks nothing like an error — it
is easy to mistake for a parsing bug.

Phase 2 replaces this file's role entirely: the EDGAR worker writes `issuers`
and `filings` from the same sources, on a schedule, with the accession number
as the idempotency key.
