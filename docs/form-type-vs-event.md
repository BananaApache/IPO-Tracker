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

**The measurement.** Scanning 103 EDGAR index days across 150 days:

| | |
|---|---|
| `8-A` filings (exchange registration) | 819 |
| distinct CIKs filing one | 490 |
| present in our issuer table | 199 |
| **excluded by `ipo_candidate_issuers`** | **195** |
| included as candidates | 4 |

**195 of 199 real listing events were being thrown away.** The heuristic was
correct about each individual issuer — they *did* have tickers — and wrong about
what that meant, because a company acquires a ticker precisely *by* completing
the event the study exists to observe. The filter excluded issuers for having
done the thing being measured.

**The fix.** Migration 005 replaces absence with evidence. An `8-A` is a filing
that says "these securities are being registered on an exchange" — it is the
event, not a proxy for it. `listing_events` records one row per `8-A` filer in
every terminal state, so the funnel from registration to trading is auditable
rather than inferred.

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

The last row is the same lesson applied preemptively: the first draft of the
event rule used "no prior price history", which a post-bankruptcy re-listing
satisfies (AZUL, NYSE-listed since 2017, first available bar two days after its
`8-A`). Periodic reports are evidence of having been a reporting company;
missing price history is merely an absence.

**Absence-based tests fail in the direction that is hardest to notice** — they
produce a clean-looking, smaller dataset rather than an error.
