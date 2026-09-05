-- 002_issuer_keyset_index.sql
--
-- Deferred from 001 on purpose: at ten seeded rows an index is noise, and an
-- index nobody has measured is a guess. Phase 2 fills `issuers` from EDGAR, so
-- it earns one now.
--
-- The expression must match GET /api/v1/issuers byte for byte -- see _SORT_KEY
-- in backend/api/issuers.py. Postgres only uses an expression index when the
-- indexed expression matches the query's, so a COALESCE written differently
-- here would build an index the list endpoint silently never touches.
CREATE INDEX issuers_filed_at_keyset_idx
    ON issuers (COALESCE(first_filed_at, '-infinity'::timestamptz) DESC, id DESC);

-- The list endpoint's other access path is ?status=. Postgres can combine this
-- with the keyset index via a bitmap scan; a composite covering both was not
-- used because status has four values and low selectivity, so leading with it
-- would help only the rarest filter.
CREATE INDEX issuers_status_idx ON issuers (status);
