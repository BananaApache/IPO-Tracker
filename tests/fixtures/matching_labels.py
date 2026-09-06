"""Hand-labelled entity-resolution set.

Labelled by reading every item, before the matcher existed.

Corpus: 40,000 Hacker News stories and comments, 2026-09-03 to 2026-09-06,
fetched by time window rather than by searching issuer names. Searching would
have handed the matcher a set some other matcher already filtered.

Two populations, both labelled exhaustively rather than sampled:

* **All 116 items** in the corpus containing any issuer alias token (word-bounded
  substring, aliases of 3+ characters). This is the entire candidate-bearing
  population, so recall over the corpus is exact rather than estimated.
* **60 items drawn at random from the 39,884 containing no alias token at all.**
  Included because that is the population the matcher actually runs against:
  measuring precision only on candidate-bearing items would score it on a
  distribution it never sees. 99.7% of real traffic looks like this bucket.

Ground truth: 5 true positives out of 176 labelled items. Every one is Oura.

LABELLING DECISION worth arguing with: a product conversation counts as a
mention of the issuer. "Oura's rings found their way onto fingers" is labelled a
true match even though nobody in that thread is discussing an IPO. The hype
score measures *attention on the company*, and product buzz is attention. The
alternative reading -- only IPO-context mentions count -- would make the metric
"IPO chatter volume", which is a different and much narrower signal. The
`ipo_context` flag below records which is which so the choice can be revisited
without relabelling.

KNOWN BLIND SPOT: an item referring to an issuer by a name form absent from the
alias table would be missed by the substring net used to find candidates, and so
would never have been labelled. Recall is therefore exact with respect to the
alias table, not with respect to reality.
"""

# uid -> (issuer CIK, is the mention in an IPO/financial context?)
TRUE_MATCHES: dict[str, tuple[str, bool]] = {
    "49558235": ("0002133022", True),   # "Oura S-1"
    "49560883": ("0002133022", True),   # "Oura's IPO Reveals High Growth for Smart-Ring Maker"
    "49576435": ("0002133022", True),   # "Health-tracking smart ring maker Oura to list on Nasdaq"
    "49576498": ("0002133022", False),  # "Oura's rings found their way onto fingers" -- product
    "49582064": ("0002133022", False),  # same thread, "Their ads totally turned me off" -- product
}

# Everything else in the labelled set is a negative. Recorded as a count rather
# than 171 uids; the uids live in the sample files alongside the corpus.
NEGATIVES_CANDIDATE_BEARING = 111   # of the 116 items containing an alias token
NEGATIVES_NOISE = 60                # of the 39,884 containing none

# The false-positive families seen while labelling, each traced to the alias
# that produced it. These are the matcher's actual adversaries.
FALSE_POSITIVE_FAMILIES = {
    "laser":      "Laser Photonics Corp -- 'Household Laser Cuts', '20-kilowatt laser'",
    "advance":    "Advance JV Group Ltd -- 'in advance', 'advance user'",
    "aura":       "an Aura-branded issuer -- 'aura farming', Salesforce Aura, a Rust agent named Aura",
    "devonian":   "a Devonian-branded issuer -- 'Lower Devonian' geological period",
    "tailored":   "a Tailored-branded issuer -- 'a strategy tailored to'",
    "inflection": "Inflection Point Acquisition Corp -- 'the real inflection point'",
    "grande":     "Grande Group Ltd -- 'Rio Grande'",
    "legion":     "Legion Capital Acquisition Corp -- 'a legion of'",
    "sensei":     "Sensei Harbor Corp -- unrelated use",
}

# Baseline to beat: word-bounded substring matching over the alias table.
NAIVE_SUBSTRING_PRECISION = 5 / 116   # 0.043
