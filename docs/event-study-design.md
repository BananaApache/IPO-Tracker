# Event study: proposed design

**Status: proposal. Nothing here is implemented.** Written before code so the
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
| **c** | the ticker has no price bar more than 5 trading days before `D₈ₐ` | the evidentiary discriminator, and the one that replaces guesswork |
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

### Known failure mode: re-listings look identical to IPOs

**AZUL** passes all four conditions — `8-A` 2026-05-26, first bar 2026-05-28, 70
bars total — but Azul S.A. has been NYSE-listed since 2017. It re-listed after
restructuring, so its price history genuinely starts two days after the `8-A`.

Ticker reuse and post-bankruptcy re-listing are **indistinguishable** from an IPO
under any rule built on "no prior price history". Mitigation: flag any event
whose issuer has EDGAR filings predating the `8-A` by more than three years as
`needs_review`, and hand-check. A re-listing has completely different return
dynamics, so including one silently would corrupt the study; flagging costs a
few manual decisions.

---

## 2. Price source: Yahoo Finance chart endpoint

**Recommendation: `query1.finance.yahoo.com/v8/finance/chart/{ticker}` via
`httpx`.**

Why:

- **No new dependency.** `yfinance` is a wrapper over this exact endpoint; adding
  it would need sign-off and buys nothing.
- **Verified working** for every ticker probed — SPCX, GPRO, LIME, AZUL, LGL,
  OPTT, APMD — returning daily bars with `open`, and a 5-year range for the
  discriminator in rule (c).
- **One request per ticker** gives the whole 90-day window plus the history
  needed for (c).

Why not the alternatives:

- **Stooq — ruled out on policy grounds, not technical ones.** It serves a
  JavaScript proof-of-work challenge; retrieving a CSV requires computing a
  SHA-256 nonce to satisfy browser verification. That is defeating an access
  control, which `PROJECT_BRIEF.md` §7 prohibits. Confirmed by probe.
- **Alpha Vantage** — free tier is 25 requests/day. ~195 events would take eight
  days to backfill once.

**Honest caveat.** Yahoo's chart endpoint is undocumented and carries no terms
granting this use, unlike SEC EDGAR. It is not circumvention — no auth, no
challenge, no UA spoofing, a plain request gets a plain JSON answer — but it is
not a licensed feed either. If you want a source with explicit terms, **Tiingo**
or **Polygon** free tier (5 req/min, workable for 195 events) are the options and
I would switch. Rate limiting goes through the existing `RetryingClient` at 1
req/sec, one limiter, same as every other source.

---

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
