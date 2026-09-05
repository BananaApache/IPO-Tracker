-- 003_offering_extraction_provenance.sql
--
-- Everything in `offerings` is read out of unstructured prospectus cover text,
-- so every value needs to carry how it was obtained. A price with no record of
-- its derivation is a number nobody can defend.

ALTER TABLE offerings
    -- 0..1. Which pattern matched, how close it sat to the anchor phrase, and
    -- whether the figure survived the plausibility bounds.
    ADD COLUMN extraction_confidence NUMERIC(3, 2)
        CHECK (extraction_confidence BETWEEN 0 AND 1),

    -- The specific rule that fired ('ipo_range_between', 'final_price_424b4',
    -- ...) or the reason nothing was written ('rejected_per_unit',
    -- 'rejected_out_of_bounds', 'no_anchor'). Names a code path, so a surprising
    -- row can be traced to the rule that produced it.
    ADD COLUMN extraction_method TEXT,

    ADD COLUMN extracted_at TIMESTAMPTZ,

    -- "The issuer has not set a range yet" and "we could not read the range"
    -- are different facts, and Phase 4 has to tell them apart: the first is an
    -- early-stage issuer, the second is a gap in our pipeline. Initial S-1s
    -- routinely print the sentence with the figures left blank, so this is the
    -- common case, not an error case.
    ADD COLUMN price_disclosure TEXT NOT NULL DEFAULT 'not_found'
        CHECK (price_disclosure IN ('disclosed', 'not_yet_disclosed', 'not_found'));

-- One offering per issuer, so an amendment updates the deal terms rather than
-- inserting a rival row. Needed as a real constraint because that upsert is
-- ON CONFLICT, and ON CONFLICT requires a unique index to arbitrate on.
--
-- The assumption this bakes in: an issuer has exactly one offering in flight.
-- True for the IPO window this project watches; a later follow-on offering
-- would need this revisited.
ALTER TABLE offerings ADD CONSTRAINT offerings_issuer_key UNIQUE (issuer_id);

COMMENT ON COLUMN offerings.extraction_confidence IS
    'Confidence in the extracted price fields, 0..1. NULL when no price was '
    'written. Values are only stored above the acceptance threshold -- a figure '
    'that fails the plausibility bounds is rejected outright rather than kept '
    'at low confidence, because a wrong price still reaches a chart.';

-- Per-bank rather than per-offering: a cover can name a lead with certainty and
-- a co-manager ambiguously in the same table.
ALTER TABLE offering_underwriters
    ADD COLUMN confidence NUMERIC(3, 2) CHECK (confidence BETWEEN 0 AND 1);

-- Roles are not always printed. A name found with no role header is real
-- information and worth keeping; inventing 'lead' for it is not.
ALTER TABLE offering_underwriters ALTER COLUMN role DROP NOT NULL;
