# The recurring bug: form type is not a corporate event

Three separate defects in this project, each caught by measurement rather than
review, all the same root confusion: **treating the presence of a filing type as
evidence of a corporate event.**

Worth writing down because the third instance was the largest, and because the
fix in each case was the same shape — replace an inference with evidence.

---

## 1. A 424B4 does not mean the deal priced

**The bug.** Phase 2a ingestion set `status = 'priced'` on any `424B4`, reasoning
that a final prospectus means pricing happened.

**The measurement.** Of the seven issuers it marked `priced`, **six already had
Nasdaq tickers.** The one inspected was a prospectus supplement from First
Breach, Inc. (`FBDT`) attaching a quarterly report; its only dollar figure was
the previous day's closing price of already-traded stock.

**Why.** `424B4` covers shelf takedowns, at-the-market programmes and resale
prospectuses from companies that have traded for years. The form says "final
prospectus", not "IPO priced".

**The fix.** Ingestion stopped inferring status from form type entirely.
Advancing past `filed` now needs prospectus evidence.

---

## 2. The backfill population

**The bug.** The Phase 1 seed excluded already-listed companies by hand. The
150-day EDGAR backfill applied no such filter, because "filed an S-1" was
treated as equivalent to "is an IPO candidate".

**The measurement.** **566 of 733 issuers (77%) arrived already trading.** A
listed company files resale and shelf registrations routinely; the `S-1` family
is not exclusive to IPOs.

**The fix.** Migration 004 added `ipo_candidate_issuers` as one canonical
definition, so the boundary could not be re-derived per stage.

---

## 3. The heuristic that excluded almost every real event

**The bug.** `ipo_candidate_issuers` defined a candidate by *absence*: no ticker,
no exchange, has a registration. Absence of evidence stood in for evidence.

**The measurement.** Scanning 103 EDGAR index days across 150 days found 819
`8-A` filings from 490 distinct CIKs. Of those, **199 were in our issuer table
and 195 of the 199 were excluded** by `ipo_candidate_issuers` — it admitted 4.

The heuristic was correct about each individual issuer: they *did* have tickers.
It was wrong about what that meant, because **a company acquires a ticker
precisely by completing the event the study exists to observe.** The filter
excluded issuers for having done the thing being measured.

**The fix and its reconciliation.** Migration 005 replaces absence with
evidence. Tracking the `8-A` family and building `listing_events` from it gives a
chain that has to be read carefully, because the 199 above is *not* the final
count:

| step | count | |
|---|---|---|
| issuers with an `8-A` in the window | **479** | after `8-A` was added to tracked forms, which created 291 new issuers (733 → 1,024) |
| − no registration statement anywhere in EDGAR | −163 | an `8-A` with no `S-1`/`F-1`/`424B` behind it is not an offering |
| = **listing events** | **316** | |
| − re-listings (prior periodic reports) | −121 | |
| = **first listings** | **195** | |
| with a ticker, so a price can be fetched | **185** | |

The 199 figure in the original measurement was the subset of `8-A` filers that
already had registration data *in our tables*. It is not the event count. Two
things separate them:

- Adding `8-A` to tracked forms **created 291 issuers** who had never appeared
  in a registration-only backfill. Most are genuine — 118 of the 316 events have
  a registration statement in EDGAR that predates our backfill window.
- Condition (b) of the event rule was proposed and then **not implemented** in
  the first pass, so the initial detection run returned 479 events rather than
  316. It now checks the submissions feed rather than our own filings table:
  requiring a registration *here* would reject a genuine IPO whose `S-1` predates
  the backfill and call a coverage gap a corporate fact.

**Measured against an independent source.** Finnhub's IPO calendar lists 162
priced IPOs in the same window. Of those, **160 appear in our detection** — 155
as first listings and 5 classified as re-listings by design (Eloxx with 71 prior
periodic reports, National Healthcare Properties with 53). Two were genuinely
missed, both SPAC unit tickers. So detection recall against an independent
calendar is **160/162 = 0.99**, and the 5 disagreements are definitional rather
than failures: Finnhub's calendar includes uplistings.

Precision is **155/195 = 0.79**, and the 40 disagreements decompose:

- **10 are ETFs and trusts** — Bitwise Hyperliquid ETF, Grayscale, VanEck BNB,
  iShares Bitcoin, Morgan Stanley Ethereum Trust. They file an `S-1` and an
  `8-A12B` to list on an exchange and satisfy every condition of the rule, but
  they are not IPOs of operating companies: no fundamentals, no underwriter
  syndicate in the usual sense, and returns that track an underlying asset.
  Including them would corrupt the study. A clean discriminator exists — all
  nine crypto vehicles carry SIC *Commodity Contracts Brokers & Dealers*, a
  sector that appears nowhere else in the cohort.
- **30 are small listings Finnhub does not cover.** Whether those are our false
  positives or its coverage gaps is not established.

---

## The pattern

In all three cases the code asked *"what form was filed?"* when the question was
*"what happened to this company?"*. Filing types are chosen by issuers for legal
reasons and reused across completely different corporate situations. The reliable
signals turned out to be:

| question | unreliable proxy | evidence used instead |
|---|---|---|
| did the deal price? | a `424B4` exists | a price extracted from the prospectus |
| is this an IPO candidate? | an `S-1` exists | no ticker **and** no exchange **and** a registration |
| did it start trading? | it has no ticker yet | an `8-A`, plus a first price bar |
| is this a first listing? | no prior price history | no prior `10-K`/`10-Q`/`20-F`/`40-F` |
| is this an offering at all? | an `8-A` exists | an `8-A` **plus** a registration statement in EDGAR |
| is this an operating company? | it filed like one | SIC code — ETFs and trusts file identically |

The last row is the same lesson applied preemptively: the first draft of the
event rule used "no prior price history", which a post-bankruptcy re-listing
satisfies (AZUL, NYSE-listed since 2017, first available bar two days after its
`8-A`). Periodic reports are evidence of having been a reporting company;
missing price history is merely an absence.

**Absence-based tests fail in the direction that is hardest to notice** — they
produce a clean-looking, smaller dataset rather than an error.
