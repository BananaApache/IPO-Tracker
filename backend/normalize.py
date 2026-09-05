"""Company-name normalisation.

`issuers.normalized_name` is the key entity resolution matches against, so this
has to be deterministic and stable: if it changes, every stored alias stops
lining up with it.

This is the minimum needed to populate the column. Phase 3 builds the real
matching pipeline (candidate generation, scoring, thresholds) on top of it and
owns any expansion.
"""

import re

# Legal-form suffixes carry no identifying information -- every third company
# ends in "Inc." -- and they vary between how a company files ("SB Energy,
# Inc.") and how anyone refers to it ("SB Energy"). Stripping them is what lets
# those two forms compare equal.
_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "llc", "lp", "llp", "plc", "sa", "nv", "bv",
    "ag", "gmbh", "ab", "oyj", "oy", "as", "pte", "pty",
    "holdings", "holding", "group",
})

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_company_name(name: str) -> str:
    text = name.lower().replace("&", " and ")
    text = _NON_ALNUM.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    tokens = text.split()
    # Strip suffixes only from the end. "Group 1 Automotive" must keep its
    # leading "Group"; only a trailing one is a legal form.
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()

    return " ".join(tokens)
