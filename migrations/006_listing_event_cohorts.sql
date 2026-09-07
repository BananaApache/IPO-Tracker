-- 006_listing_event_cohorts.sql
--
-- Which listing events belong in the study, and why each excluded one does not.
-- Stored as a classification rather than applied as a filter, so every excluded
-- cohort stays queryable and the exclusion is auditable.

ALTER TABLE listing_events
    ADD COLUMN cohort TEXT NOT NULL DEFAULT 'operating'
        CHECK (cohort IN ('operating', 'spac', 'etf_or_trust', 're_listing')),
    ADD COLUMN cohort_reason TEXT;

CREATE INDEX listing_events_cohort_idx ON listing_events (cohort);

COMMENT ON COLUMN listing_events.cohort IS
    'operating    = an operating company going public; the study cohort. '
    'spac         = blank-check company. Units price at $10 against a trust '
    '               floor, so the return distribution is structurally '
    '               different and pooling would test the thesis on two '
    '               instruments at once. 85 of 195 first listings. '
    'etf_or_trust = an exchange-traded product. Files an S-1 and an 8-A12B '
    '               identically to an IPO, but has no revenue, no underwriter '
    '               syndicate and no fundamentals -- its return tracks an '
    '               underlying asset. '
    're_listing   = already a reporting company before its 8-A (see the '
    '               periodic-report test in migration 005).';

-- The study cohort, defined once. Every stage that needs it selects here, for
-- the same reason ipo_candidate_issuers exists: this boundary has already
-- caused three bugs when re-derived per stage.
CREATE VIEW study_cohort AS
    SELECT * FROM listing_events
    WHERE cohort = 'operating' AND status = 'listed';

COMMENT ON VIEW study_cohort IS
    'Confirmed-trading operating-company listings. The event study runs on '
    'this and nothing else.';
