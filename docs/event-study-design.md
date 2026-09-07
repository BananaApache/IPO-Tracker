# Event study: proposed design

**Status: schema implemented (migration 005). Ingestion pending a market-data key.** Written before code so the
event definition and the price handling can be argued with — a wrong return is
invisible.

---

## 1. The IPO event rule

### What the data says

Scanning 103 EDGAR index days over 150 days:

| | |
|---|---|
| `8-A` filings | 819 |
| distinct CIKs filing one | 490 |
| of those, present in our issuer table | **199** |
| currently **excluded** by `ipo_candidate_issuers` | **195** |
| currently included as candidates | 4 |

So the absence-based heuristic dropped 195 issuers that filed an exchange
registration. That is the reclassification answer: **195 event candidates were
being treated as exclusions.**

### The rule

An **IPO event** exists for issuer *I* when all four hold:

| | condition | why |
|---|---|---|
| **a** | *I* filed an `8-A` (any subtype) on `D₈ₐ` | `8-A` registers securities on an exchange — the reliable "about to trade" marker |
| **b** | *I* has an `S-1`/`F-1`-family filing dated before `D₈ₐ` | excludes already-listed companies registering a *new class*. LGL Group and Ocean Power Technologies both filed `8-A`s with **1,732** and **1,756** days of prior trading |
| **c** | *I* filed **no periodic report** (`10-K`, `10-Q`, `20-F`, `40-F`) before `D₈ₐ` | the discriminator, and it needs no price feed — a company files these only once it is already a reporting company, which a first-time IPO candidate is not |
| **d** | the first price bar falls within 15 calendar days after `D₈ₐ` | stops a stale `8-A` pairing with an unrelated later listing |

**The listing date is the first price bar, not `D₈ₐ`.** An `8-A` precedes trading
by days; returns must be measured from the price that actually opened.

Verified against six known cases:

```
SPCX  8-A 2026-06-10  first bar 2026-06-12     59 bars  -> event
LIME  8-A 2026-06-26  first bar 2026-07-01     47 bars  -> event
APMD  8-A 2026-07-29  first bar 2026-07-31     26 bars  -> event
LGL   8-A 2026-06-05  first bar 2021-09-07  1,255 bars  -> rejected by (c)
OPTT  8-A 2026-06-29  first bar 2021-09-07  1,255 bars  -> rejected by (c)
```

### The 424B4 price is **not** required

Deliberate, and it changes the event count by two orders of magnitude. Only **4
offerings** currently carry an extracted price (extraction was skipped during the
backfill), so requiring one would yield ~4 events instead of ~195.

It is also unnecessary: returns are measured **from the opening price**, which
comes from the price series. The `424B4` price adds the day-one pop
(`open / ipo_price − 1`) and nothing else, so it is recorded when available and
its absence never blocks an event.

### `8-A` with no subsequent price

The case that needs an answer rather than a silent drop. Four terminal states,
all stored:

| status | meaning |
|---|---|
| `listed` | conditions a–d met; `listed_on` and `open_price` set |
| `awaiting_ticker` | `8-A` seen but EDGAR has no symbol yet, so no price can be looked up |
| `registered_no_trade` | `8-A` older than 30 days, ticker known, still no bars — postponed or pulled |
| `no_price_data` | ticker known, bars exist for peers, none for this symbol — foreign listing or thin OTC the source does not cover |

Nothing is discarded. The funnel from `8-A` to `listed` is itself a reportable
number, and a withdrawn IPO after an exchange registration is interesting in its
own right.

### Re-listings: solved with a rule, not a flag

The original draft of this document proposed flagging suspected re-listings for
manual review. That was the wrong answer — manual review does not scale past one
project, and the case matters enough to need a rule.

**AZUL** is the problem case. It filed an `8-A` on 2026-05-26 with its first
available price bar on 2026-05-28 and only 70 bars of history, so *every* test
built on "no prior price history" classifies it as an IPO. Azul S.A. has been
NYSE-listed since 2017; it re-listed after restructuring. Left in the sample it
would sit there as an outlier with a real ticker and a real `8-A`.

**The discriminator is periodic reports.** A company files `10-K`, `10-Q`, `20-F`
or `40-F` only once it is already a reporting company. A first-time IPO candidate
has none. Measured on six known cases:

| ticker | periodic reports before `8-A` | earliest | verdict |
|---|---|---|---|
| AZUL | **12** | 2018-04-27 | re-listing |
| LGL | 98 | 2004-08-12 | already listed |
| OPTT | 75 | 2009-07-14 | already listed |
| SPCX | 0 | — | genuine IPO |
| LIME | 0 | — | genuine IPO |
| APMD | 0 | — | genuine IPO |

Clean separation, no price feed required, no manual pass. It also subsumes the
price-history condition the first draft used, which is a strict improvement:
classification no longer depends on the market-data provider at all, so a gap in
the feed can no longer silently reclassify an event.

`listing_events` stores `prior_periodic_reports` and `first_periodic_report_at`
alongside the boolean, so the study can exclude re-listings as a **documented
cohort** rather than a manual decision, and a reader can check the call.

**Residual limitation:** the submissions feed returns roughly the most recent
1,000 filings. A company that stopped reporting long ago and has filed heavily
since could in principle have its old periodic reports fall outside that window.
Periodic reports are frequent enough that this is unlikely, but it is not
impossible and is not currently detected.

---

## 2. Price source: a licensed provider

**Decision: Tiingo or Polygon free tier. Not Yahoo.**

The first draft of this document recommended Yahoo's chart endpoint, on the
grounds that it is technically trivial — no auth, no challenge, no User-Agent
spoofing, and a plain request returns clean JSON for all seven tickers tested.
That recommendation was withdrawn, and the reasoning that killed it was already
in the draft: *"not circumvention, but not licensed either."*

SEC EDGAR grants this use explicitly. Yahoo grants nothing. §7 has already
rejected SerpAPI and unauthenticated Reddit on exactly that basis, and rejected
Stooq for serving a proof-of-work challenge. Being consistent is worth more than
the convenience, and 195 events at 5 requests/minute is 40 minutes once.

Rejected alternatives, for the record:

| source | why not |
|---|---|
| Yahoo chart endpoint | undocumented, grants no permission for this use |
| Stooq | JavaScript proof-of-work challenge; retrieval requires defeating browser verification |
| Alpha Vantage | 25 requests/day free tier — eight days per backfill |

**Every licensed provider requires an API key**, because a licence is granted to
an identified party. Verified: Tiingo `403`, Polygon `401`, Finnhub `401`,
Marketaux `401` without one. That is the cost of consistency here.

Rate limiting goes through the existing `RetryingClient`, one limiter for the
provider, same as every other source. **If the licensed feed has gaps on recent
listings, that gets reported rather than worked around** — no silent fallback.

## 3. Migration 005: proposed schema

```sql
CREATE TABLE listing_events (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- One listing per issuer. A re-listing violates this and the insert fails
    -- loudly rather than quietly creating a second event.
    issuer_id           BIGINT NOT NULL UNIQUE REFERENCES issuers (id) ON DELETE CASCADE,
    status              TEXT NOT NULL CHECK (status IN
                          ('listed','awaiting_ticker','registered_no_trade','no_price_data')),
    eight_a_filing_id   BIGINT REFERENCES filings (id) ON DELETE SET NULL,
    eight_a_filed_at    TIMESTAMPTZ NOT NULL,
    ticker              TEXT,
    exchange            TEXT,
    listed_on           DATE,             -- first price bar; NULL until listed
    open_price          NUMERIC(12, 4),
    ipo_price           NUMERIC(12, 4),   -- from a 424B4; nullable by design
    ipo_price_filing_id BIGINT REFERENCES filings (id) ON DELETE SET NULL,
    -- Re-listing suspicion, per the AZUL failure mode.
    needs_review        BOOLEAN NOT NULL DEFAULT FALSE,
    review_reason       TEXT,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX listing_events_listed_on_idx ON listing_events (listed_on DESC);
CREATE INDEX listing_events_needs_review_idx ON listing_events (needs_review)
    WHERE needs_review;

CREATE TABLE price_bars (
    issuer_id  BIGINT NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    day        DATE   NOT NULL,
    open       NUMERIC(12, 4),
    high       NUMERIC(12, 4),
    low        NUMERIC(12, 4),
    close      NUMERIC(12, 4),
    volume     BIGINT,
    source     TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Composite PK makes a refetch idempotent, the same structural guarantee
    -- filings.accession_no provides.
    PRIMARY KEY (issuer_id, day)
);
```

Three decisions worth challenging:

**Returns are not stored.** 30/60/90-day returns are derived from `price_bars` on
read. Storing them creates a second source of truth that can silently disagree
with the series it came from — the same reason `scores.components` records inputs
rather than duplicating outputs. Computing three returns over ~90 rows per issuer
is trivial.

**No `adjusted_close`.** Over a 90-day window on a fresh listing, splits and
dividends are rare. An adjusted series silently *changes retroactively* when a
corporate action happens, which would make a published return irreproducible. Raw
prices are wrong in a visible way instead. Worth revisiting if a split shows up.

**`price_bars` keys on `issuer_id`, not `ticker`.** Tickers get reused; issuer ids
do not. `listing_events.ticker` records which symbol the bars were fetched under,
so a reuse is traceable rather than silently merged.

### Does this replace `ipo_candidate_issuers`?

No — it complements it, and both stay defined in exactly one place:

- **`ipo_candidate_issuers`** (migration 004) remains the *pre-listing* cohort.
  Matching before an event exists still needs it.
- **`listing_events`** is the *study* cohort, and it is evidentiary rather than
  absence-based. The 8-A caveat in 004 stops being a confound: an 8-A now
  *promotes* an issuer into the study instead of quietly ejecting it from the
  candidate pool.
