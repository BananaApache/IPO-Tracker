-- 005_listing_events_and_prices.sql
--
-- The event study needs two things the schema had no home for: the moment an
-- issuer actually started trading, and the price series afterwards.

CREATE TABLE listing_events (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- One listing per issuer. A second listing for the same issuer is almost
    -- always a re-listing after restructuring, and the UNIQUE makes that insert
    -- fail loudly instead of quietly adding a second event with different
    -- return dynamics.
    issuer_id           BIGINT      NOT NULL UNIQUE REFERENCES issuers (id) ON DELETE CASCADE,

    status              TEXT        NOT NULL CHECK (status IN (
                            'listed',               -- trading; listed_on and open_price set
                            'awaiting_ticker',      -- 8-A seen, EDGAR has no symbol yet
                            'registered_no_trade',  -- 8-A over 30 days old, still no bars
                            'no_price_data'         -- ticker known, provider has no coverage
                        )),

    -- 8-A registers securities on an exchange: the reliable "about to trade"
    -- marker, and evidence rather than the absence of a ticker.
    eight_a_filing_id   BIGINT      REFERENCES filings (id) ON DELETE SET NULL,
    eight_a_filed_at    TIMESTAMPTZ NOT NULL,

    ticker              TEXT,
    exchange            TEXT,

    -- The FIRST PRICE BAR, not the 8-A date. An 8-A precedes trading by days,
    -- and a return measured from the wrong day is wrong invisibly.
    listed_on           DATE,
    open_price          NUMERIC(12, 4),

    -- From a 424B4 when extraction found one. Deliberately nullable and
    -- deliberately NOT part of the event rule: only 4 of 234 issuers with a
    -- 424B4 currently have an extracted price, so requiring it would have cut
    -- the study from ~195 events to ~4. Returns are measured from open_price;
    -- this only adds the day-one pop.
    ipo_price           NUMERIC(12, 4),
    ipo_price_filing_id BIGINT      REFERENCES filings (id) ON DELETE SET NULL,

    -- RE-LISTING DETECTION, stored as evidence rather than a suspicion.
    --
    -- A post-bankruptcy re-listing passes every test built on "no prior price
    -- history": AZUL filed an 8-A on 2026-05-26 with its first available bar on
    -- 2026-05-28, despite having been NYSE-listed since 2017. Left in the
    -- sample it would sit there as an outlier with a real ticker and a real 8-A.
    --
    -- The discriminator is periodic reports. A company files 10-K/10-Q/20-F/40-F
    -- only once it is already a reporting company, which a first-time IPO
    -- candidate is not. Measured on six known cases: AZUL 12 such filings
    -- (earliest 2018-04-27), LGL 98 (2004), OPTT 75 (2009); SPCX, LIME and APMD
    -- zero. Clean separation, no price feed required, no manual review.
    --
    -- Both columns are stored, not just the boolean, so the study can exclude
    -- these as a documented cohort and a reader can check the call.
    prior_periodic_reports  INTEGER NOT NULL DEFAULT 0,
    first_periodic_report_at DATE,
    re_listing_suspected    BOOLEAN NOT NULL DEFAULT FALSE,

    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX listing_events_listed_on_idx ON listing_events (listed_on DESC);
CREATE INDEX listing_events_status_idx ON listing_events (status);
CREATE INDEX listing_events_re_listing_idx ON listing_events (re_listing_suspected)
    WHERE re_listing_suspected;

COMMENT ON TABLE listing_events IS
    'One row per issuer that filed an 8-A. Evidentiary replacement for the '
    'absence-based ipo_candidate_issuers heuristic, which excluded 195 of the '
    '199 real listing events in a 150-day window. Rows are kept in every '
    'terminal state so the 8-A-to-listed funnel is auditable.';

-- ---------------------------------------------------------------------------
CREATE TABLE price_bars (
    -- Keyed on issuer_id, not ticker: tickers get reused, issuer ids do not.
    -- listing_events.ticker records which symbol these were fetched under, so a
    -- reuse is traceable rather than silently merged into one series.
    issuer_id  BIGINT      NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    day        DATE        NOT NULL,
    open       NUMERIC(12, 4),
    high       NUMERIC(12, 4),
    low        NUMERIC(12, 4),
    close      NUMERIC(12, 4),
    volume     BIGINT,
    -- Which provider supplied this bar. Two providers disagreeing on the same
    -- day is a fact worth being able to see.
    source     TEXT        NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Composite PK makes a refetch idempotent -- the same structural guarantee
    -- filings.accession_no provides for filings.
    PRIMARY KEY (issuer_id, day)
);

COMMENT ON TABLE price_bars IS
    'Raw daily bars, unadjusted. Returns are NOT stored anywhere: they are '
    'derived from this series on read, because a stored return can silently '
    'disagree with the prices it came from. No adjusted_close either -- an '
    'adjusted series changes retroactively when a corporate action happens, '
    'which would make a published return irreproducible. Raw prices are wrong '
    'visibly instead.';
