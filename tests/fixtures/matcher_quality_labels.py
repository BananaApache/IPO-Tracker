"""Hand labels for the MATCHER-QUALITY evaluation.

This measures text matching, not IPO-signal availability. The two questions were
tangled together in Phase 3b and are now separate:

  * **Matcher quality** (this file) runs against every alias, including the 566
    already-listed issuers. That is where the hard cases live -- Track Group,
    Click Holdings, Fast Track Group, Andersen Group -- so it is the honest test
    of the scoring.
  * **Pre-IPO signal availability** is reported separately in docs/matching.md
    as n=1. It is a finding, not a score.

Sampled from a 90-day Hacker News corpus (1,004,502 items), not stratified:
100 drawn at random from the 2,072-item accept pool, 40 from the 1,039-item
review pool, 40 from the alias-bearing items the scoring drops outright. Labels
recorded by reading each item.

Pools have different sizes, so any pooled figure needs reweighting -- see
docs/matching.md, which reports per-pool rates rather than one number.
"""

# Indices into the sampled accept list (see scratchpad matcher_sample.json,
# regenerable with the seed in the eval script).
ACCEPT_TRUE = {
    # GoPro, Inc. -- "Is this the end of the once-mighty GoPro?" and a thread
    # about indexing GoPro footage. Real mentions of a real company.
    1, 7, 8, 11, 12, 13, 14, 16, 18, 19, 21, 22, 24, 29, 31, 37, 39, 43, 45,
    46, 48, 51, 55, 56, 57, 58, 59, 63, 64, 66, 70, 75, 78, 79, 83, 88, 89,
    90, 91, 94, 95, 98,
    # SK hynix Inc. -- memory shortage and price-fixing coverage.
    2, 5, 9, 15, 20, 28, 30, 32, 36, 38, 44, 54, 68, 72, 73, 86, 96, 97, 99,
    # GOLDMAN SACHS GROUP INC -- named in AI-economics and SpaceX IPO stories.
    6, 26, 27, 40, 47, 49, 85,
    23,  # Oura Inc. -- "The Oura Ring costs $350 now"
    10,  # Medline Inc. -- the Nasdaq medical-supplies company; warehouse fire
}
ACCEPT_SAMPLE_SIZE = 100
ACCEPT_POOL = 2072

# False-positive families in the accept sample, with what they actually matched.
ACCEPT_FALSE_FAMILIES = {
    "andersen": "Andersen Group Inc. -- 'Danish privacy activist Lars Andersen raided by police' (21 of 30 FPs)",
    "kepler":   "Kepler Group Ltd -- the Nvidia Kepler GPU architecture, and Kepler's equation",
    "neutron":  "Neutron Holdings, Inc. (Lime) -- the subatomic particle, in reactor and solar threads",
    "azul":     "AZUL SA -- 'azulejo', Portuguese for blue",
    "stewards": "Stewards, Inc. -- the ordinary word 'steward'",
    "reformation": "Reformation Inc. -- historical//general use",
    "capstone": "Capstone Holding Corp. -- 'a law school capstone paper'",
}

# Review band (0.45 <= score < 0.70).
REVIEW_TRUE = {1, 6, 8, 19, 20, 22, 30, 35, 36, 38, 39}  # all Bending Spoons S.p.A.
REVIEW_SAMPLE_SIZE = 40
REVIEW_POOL = 1039

# Items with an alias candidate that the scoring drops entirely.
REJECT_TRUE_COUNT = 0
REJECT_SAMPLE_SIZE = 40
REJECT_POOL = 161_013  # alias-bearing items across the 90-day corpus
