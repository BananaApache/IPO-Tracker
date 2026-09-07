"""The IPO-candidate cohort.

The definition itself lives in the database, as the view created by migration
004. This module exists so Python code refers to it by one name instead of
spelling out a WHERE clause -- see that migration for the rule, the reasons, and
the two caveats.
"""

# Every stage that needs the IPO-candidate distinction reads from here.
CANDIDATE_VIEW = "ipo_candidate_issuers"

CANDIDATE_ALIAS_SQL = f"""
    SELECT a.id, a.issuer_id, a.normalized_alias, a.kind
    FROM aliases a
    WHERE a.issuer_id IN (SELECT id FROM {CANDIDATE_VIEW})
"""

# Issuers the study needs attention for: IPO candidates that have not listed
# yet, plus every detected listing event. Matching only against candidates left
# the 108 confirmed listings with no mentions at all -- an issuer leaves that
# view the moment it acquires a ticker, which is the moment the event study
# starts caring about it.
STUDY_ALIAS_SQL = f"""
    SELECT a.id, a.issuer_id, a.normalized_alias, a.kind
    FROM aliases a
    WHERE a.issuer_id IN (SELECT id FROM {CANDIDATE_VIEW})
       OR a.issuer_id IN (SELECT issuer_id FROM listing_events)
"""

# Deliberately separate. Matcher *quality* is a text problem and the hard cases
# (Track Group, Click Holdings, Fast Track Group) are all already-listed
# companies, so the evaluation runs against every alias. Production matching
# runs against the cohort. Conflating the two is what made a perfect score on
# 126 aliases look meaningful.
ALL_ALIAS_SQL = """
    SELECT a.id, a.issuer_id, a.normalized_alias, a.kind FROM aliases a
"""
