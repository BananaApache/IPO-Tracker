-- 001_initial_schema.sql
--
-- Core schema for IPO surveillance: who is filing (issuers, filings, offerings),
-- what people are saying about them (mentions), and what we compute from both
-- (fundamentals, scores).
--
-- Convention used throughout this file:
--   * Status/kind/role columns are TEXT + CHECK rather than Postgres ENUM.
--     ENUMs cannot have values removed and ALTER TYPE has transaction
--     restrictions, which fights a hand-rolled migration runner. A CHECK is
--     edited by a normal ALTER TABLE in a later numbered migration.
--   * Money and ratios are NUMERIC, never float. Binary floating point cannot
--     represent 0.1 exactly; a price range that drifts is not defensible.
--   * Identity columns over `serial`: `serial` is a legacy shorthand that leaves
--     the sequence's ownership implicit.

-- ---------------------------------------------------------------------------
-- issuers: one row per company that has filed to go public.
-- ---------------------------------------------------------------------------
CREATE TABLE issuers (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- CIK is SEC's permanent issuer key and our upsert target in Phase 2.
    -- TEXT, not an integer, because it is canonically zero-padded to 10 digits
    -- ('0001640147') and losing the padding breaks EDGAR URL construction.
    cik             TEXT        NOT NULL UNIQUE,
    legal_name      TEXT        NOT NULL,
    -- Lowercased, punctuation- and suffix-stripped form. Denormalized on
    -- purpose: entity resolution (Phase 3) matches against this on every
    -- Reddit post, so it must be indexable rather than computed per query.
    normalized_name TEXT        NOT NULL,
    -- NULL until the issuer actually lists. This is the whole premise of the
    -- project: we track companies during the window when no ticker exists yet.
    ticker          TEXT,
    exchange        TEXT,
    sector          TEXT,
    status          TEXT        NOT NULL DEFAULT 'filed'
                                CHECK (status IN ('filed', 'priced', 'listed', 'withdrawn')),
    first_filed_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Required by the brief; supports name-based candidate lookup during matching.
CREATE INDEX issuers_normalized_name_idx ON issuers (normalized_name);

-- ---------------------------------------------------------------------------
-- aliases: the surface forms an issuer is referred to by. Powers entity
-- resolution -- one issuer has many.
-- ---------------------------------------------------------------------------
CREATE TABLE aliases (
    id               BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issuer_id        BIGINT      NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    alias            TEXT        NOT NULL,
    normalized_alias TEXT        NOT NULL,
    kind             TEXT        NOT NULL
                                 CHECK (kind IN ('legal', 'brand', 'cashtag', 'informal')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Not in the brief's column list, but the Phase 3 alias generator is meant
    -- to be re-runnable. Without this it would duplicate every alias per run.
    UNIQUE (issuer_id, normalized_alias, kind)
);

-- Required by the brief; this is the hot lookup during mention matching.
CREATE INDEX aliases_normalized_alias_idx ON aliases (normalized_alias);

-- ---------------------------------------------------------------------------
-- filings: raw EDGAR submissions. Append-only audit trail of what we fetched.
-- ---------------------------------------------------------------------------
CREATE TABLE filings (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issuer_id       BIGINT      NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    -- Denormalized from issuers so an ingest worker can write a filing row
    -- using only what EDGAR handed it, without a join back.
    cik             TEXT        NOT NULL,
    -- EDGAR's per-submission unique id. UNIQUE makes the Phase 2 poller
    -- idempotent: re-polling the same feed is an ON CONFLICT DO NOTHING.
    accession_no    TEXT        NOT NULL UNIQUE,
    form_type       TEXT        NOT NULL,
    filed_at        TIMESTAMPTZ NOT NULL,
    primary_doc_url TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Serves GET /api/v1/issuers/{id}/filings, which reads newest-first.
CREATE INDEX filings_issuer_filed_at_idx ON filings (issuer_id, filed_at DESC);

-- ---------------------------------------------------------------------------
-- offerings: the deal terms, which move as amendments land.
-- ---------------------------------------------------------------------------
CREATE TABLE offerings (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issuer_id          BIGINT NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    price_low          NUMERIC(12, 4),
    price_high         NUMERIC(12, 4),
    -- NULL until pricing night; the gap between range and final is signal.
    price_final        NUMERIC(12, 4),
    shares_offered     BIGINT,
    float_shares       BIGINT,
    expected_list_date DATE,
    lockup_expires_at  DATE,
    -- SET NULL not CASCADE: losing the provenance filing should not delete the
    -- deal terms we already extracted from it.
    source_filing_id   BIGINT REFERENCES filings (id) ON DELETE SET NULL,
    CONSTRAINT offerings_price_range_ordered CHECK (
        price_low IS NULL OR price_high IS NULL OR price_low <= price_high
    )
);

CREATE INDEX offerings_issuer_idx ON offerings (issuer_id);

-- ---------------------------------------------------------------------------
-- underwriters + offering_underwriters: bank syndicate per deal.
-- Underwriter tier feeds the Phase 4 quality score.
-- ---------------------------------------------------------------------------
CREATE TABLE underwriters (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT   NOT NULL,
    -- UNIQUE on the normalized form so 'Goldman, Sachs & Co.' and
    -- 'Goldman Sachs & Co. LLC' collapse to one bank rather than two tiers.
    normalized_name TEXT   NOT NULL UNIQUE
);

CREATE TABLE offering_underwriters (
    offering_id    BIGINT NOT NULL REFERENCES offerings (id) ON DELETE CASCADE,
    underwriter_id BIGINT NOT NULL REFERENCES underwriters (id) ON DELETE CASCADE,
    role           TEXT   NOT NULL CHECK (role IN ('lead', 'co_manager')),
    -- Composite PK, no surrogate id: a bank appears once per offering.
    PRIMARY KEY (offering_id, underwriter_id)
);

-- The PK indexes offering_id; this covers the reverse lookup ("what did this
-- bank lead?"), which the quality score needs to compute tiers.
CREATE INDEX offering_underwriters_underwriter_idx ON offering_underwriters (underwriter_id);

-- ---------------------------------------------------------------------------
-- mentions: unstructured chatter, matched to an issuer with a confidence.
--
-- Design point from the brief: issuer_id is NULLABLE. Unmatched and
-- low-confidence rows are KEPT, not discarded, so match precision stays
-- auditable and the review queue has something to show.
-- ---------------------------------------------------------------------------
CREATE TABLE mentions (
    id               BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- One value per SourceAdapter.name (Phase 3). 'reddit' is listed because
    -- the adapter is designed for, but gated on, approved OAuth access --
    -- see the hard constraint in PROJECT_BRIEF.md section 7.
    source           TEXT          NOT NULL CHECK (source IN ('hn', 'gdelt', 'reddit')),
    -- The source's own id (Reddit fullname, article guid).
    source_uid       TEXT          NOT NULL,
    -- NULL = ingested but not resolved to any issuer. See note above.
    issuer_id        BIGINT        REFERENCES issuers (id) ON DELETE SET NULL,
    -- Which alias fired, so a bad match can be traced to the rule that made it.
    matched_alias_id BIGINT        REFERENCES aliases (id) ON DELETE SET NULL,
    match_confidence NUMERIC(3, 2) CHECK (match_confidence BETWEEN 0 AND 1),
    needs_review     BOOLEAN       NOT NULL DEFAULT FALSE,
    -- Salted SHA-256 of the platform username -- never the username itself.
    -- Reddit's Responsible Builder Policy prohibits re-identifying users or
    -- deriving user characteristics, and the only thing this project needs
    -- from an author is whether two mentions came from the same one
    -- (mention_daily.unique_authors). A salted hash answers exactly that
    -- question and nothing else. The salt lives in MENTION_HASH_SALT, outside
    -- the database, so a database leak alone cannot rebuild the usernames by
    -- hashing a dictionary of known handles.
    author_hash      TEXT,
    channel          TEXT,
    title            TEXT,
    -- Excerpt, not full body: we only need enough to justify a match in the
    -- review UI, and storing whole posts is someone else's copyright problem.
    body_excerpt     TEXT,
    url              TEXT,
    engagement_score INTEGER       NOT NULL DEFAULT 0,
    posted_at        TIMESTAMPTZ   NOT NULL,
    ingested_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    -- Makes re-ingesting an overlapping window idempotent.
    UNIQUE (source, source_uid)
);

-- Required by the brief; serves the per-issuer mention time series.
CREATE INDEX mentions_issuer_posted_at_idx ON mentions (issuer_id, posted_at DESC);

-- Required by the brief. PARTIAL index: the review queue is a tiny slice of a
-- large table, so indexing only the TRUE rows keeps it small and hot.
CREATE INDEX mentions_needs_review_idx ON mentions (needs_review) WHERE needs_review;

-- Supports the retention sweep described below, which deletes by age. Without
-- it that DELETE degrades into a sequential scan of the largest table here.
CREATE INDEX mentions_posted_at_idx ON mentions (posted_at);

-- Retention, recorded in the schema itself so it survives in a plain pg_dump
-- and shows up under \d+ for anyone auditing the database directly.
COMMENT ON TABLE mentions IS
    'Raw social mentions. RETENTION: individual rows are deleted 90 days after '
    'posted_at by a scheduled sweep (built in Phase 3). The mention_daily '
    'aggregates derived from them are permanent -- the long-run signal lives '
    'there, so nothing of analytical value depends on keeping raw rows longer. '
    'Deleting the raw row is what makes this a metrics pipeline rather than an '
    'archive of other people''s posts.';

COMMENT ON COLUMN mentions.author_hash IS
    'Salted SHA-256 of the platform username. The raw username is never stored. '
    'Used only for distinct-author counts; not reversible without the salt, '
    'which is held outside the database.';

-- ---------------------------------------------------------------------------
-- mention_daily: nightly rollup. The hype score reads this, never `mentions`.
-- ---------------------------------------------------------------------------
CREATE TABLE mention_daily (
    issuer_id           BIGINT         NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    day                 DATE           NOT NULL,
    source              TEXT           NOT NULL CHECK (source IN ('hn', 'gdelt', 'reddit')),
    mention_count       INTEGER        NOT NULL DEFAULT 0,
    -- Counted separately from mention_count: ten posts from one account is
    -- a different signal than ten posts from ten accounts.
    unique_authors      INTEGER        NOT NULL DEFAULT 0,
    weighted_engagement NUMERIC(14, 4) NOT NULL DEFAULT 0,
    -- PK rather than a surrogate id + UNIQUE. Gives the brief's uniqueness
    -- guarantee and is the natural upsert target for the nightly rollup.
    PRIMARY KEY (issuer_id, source, day)
);

-- Required by the brief; serves cohort-wide "last N days" scans at score time.
CREATE INDEX mention_daily_day_idx ON mention_daily (day DESC);

COMMENT ON TABLE mention_daily IS
    'Permanent daily aggregates. Deliberately outlives the mentions rows it is '
    'computed from (see the 90-day retention note on mentions), which is why '
    'unique_authors is materialised here rather than recomputed on demand.';

-- ---------------------------------------------------------------------------
-- fundamentals: numbers extracted from filings. Feeds the quality score.
-- ---------------------------------------------------------------------------
CREATE TABLE fundamentals (
    id               BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issuer_id        BIGINT        NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    period_end       DATE          NOT NULL,
    revenue          NUMERIC(20, 2),
    -- Prior-period revenue stored alongside rather than looked up, because
    -- S-1s restate history; the comparison must use the pair as filed.
    revenue_prior    NUMERIC(20, 2),
    gross_margin     NUMERIC(6, 4),
    -- Signed: pre-IPO issuers lose money, and that is the interesting case.
    net_income       NUMERIC(20, 2),
    total_debt       NUMERIC(20, 2),
    cash             NUMERIC(20, 2),
    source_filing_id BIGINT        REFERENCES filings (id) ON DELETE SET NULL,
    extracted_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX fundamentals_issuer_period_idx ON fundamentals (issuer_id, period_end DESC);

-- ---------------------------------------------------------------------------
-- scores: precomputed by the worker, NEVER calculated per request.
-- ---------------------------------------------------------------------------
CREATE TABLE scores (
    id            BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issuer_id     BIGINT        NOT NULL REFERENCES issuers (id) ON DELETE CASCADE,
    -- DATE, not a timestamp: the worker produces one snapshot per issuer per
    -- day, and the UNIQUE below is what enforces that.
    as_of         DATE          NOT NULL,
    hype_score    NUMERIC(6, 3),
    quality_score NUMERIC(6, 3),
    gem_score     NUMERIC(6, 3),
    -- Every input that produced the three numbers above. The dashboard must be
    -- able to explain any figure it shows; a score with no breakdown is an
    -- opinion, not a measurement.
    components    JSONB         NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (issuer_id, as_of)
);

-- Serves both "latest score per issuer" and the sort= modes on the list
-- endpoint. DESC because every read wants the most recent snapshot.
CREATE INDEX scores_issuer_as_of_idx ON scores (issuer_id, as_of DESC);
