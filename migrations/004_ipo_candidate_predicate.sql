-- 004_ipo_candidate_predicate.sql
--
-- ONE canonical definition of "is this issuer an IPO candidate".
--
-- This boundary has now caused three separate bugs, each because the test was
-- re-derived at the point of use rather than looked up:
--
--   1. Phase 2a inferred status='priced' from any 424B4, but 424B4 covers shelf
--      takedowns and resale prospectuses from long-listed companies. Six of
--      seven issuers it marked 'priced' already had Nasdaq tickers.
--   2. The Phase 1 seed excluded already-listed companies by hand; the 150-day
--      backfill did not, so 566 of 733 issuers (77%) arrived already trading.
--   3. The Phase 3b matcher scored against that whole population, so 97% of
--      matched attention (3,503 of 3,613 mentions) belonged to listed companies.
--
-- So it lives in the database, as a view, and every stage selects from it.
-- A filter that is written once cannot disagree with itself.
--
-- THE RULE: an issuer is a candidate when it has neither a ticker nor an
-- exchange, and has a registration statement on file.
--
-- CAVEAT 1 -- this errs in the direction that costs recall, at the worst
-- possible moment. EDGAR populates `exchanges` when an issuer files an 8-A,
-- which happens *days before* trading begins. An issuer therefore drops out of
-- this view shortly before it lists -- which is exactly the window the whole
-- thesis cares most about. Correcting it needs the 8-A filing date or a
-- market-data source; neither is in the pipeline today. Observed concretely in
-- Phase 1: Obsidian Therapeutics carried a Nasdaq record while still pre-IPO.
--
-- CAVEAT 2 -- `ticker` and `exchange` come from EDGAR's submissions feed and
-- are only as current as the last time that issuer was polled. An issuer that
-- listed since its last poll still looks like a candidate here.
CREATE VIEW ipo_candidate_issuers AS
    SELECT *
    FROM issuers
    WHERE ticker IS NULL
      AND exchange IS NULL
      AND first_filed_at IS NOT NULL;

COMMENT ON VIEW ipo_candidate_issuers IS
    'Canonical IPO-candidate cohort: no ticker, no exchange, has a registration '
    'on file. Every stage that needs the distinction must select from here '
    'rather than re-deriving it -- see migration 004 for why, and for the two '
    'caveats (8-A listings pre-empt trading; listing data is only as fresh as '
    'the last EDGAR poll).';
