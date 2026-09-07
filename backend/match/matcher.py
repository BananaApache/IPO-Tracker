"""Resolving a mention to an issuer.

normalize -> candidate generation -> scored match -> threshold. Substring
containment is only the candidate step; on its own it is 4% precise against real
Hacker News traffic (see docs/matching.md), because company names are ordinary
words and ordinary words are what people write.

The scoring below exists to separate "this string appeared" from "this is about
that company".
"""

import re
from dataclasses import dataclass

from backend.match.common_words import COMMON_WORDS

# Terms that make a company-shaped string much more likely to be the company.
# Matched against NORMALIZED text, which has already had punctuation flattened.
# Writing "s-1" here silently never fires: normalize_text turns it into "s 1".
# That cost three of five true positives on the first evaluation run.
_FINANCIAL_CONTEXT = re.compile(
    r"(?i)(?<![a-z0-9])(ipo|s 1|f 1|424b4|prospectus|nasdaq|nyse|ticker|shares?|"
    r"stock|listing|lists|going public|valuation|underwrit[a-z]*|filing|filed|"
    r"offering|pre ipo|market cap|sec)(?![a-z0-9])"
)

# Base confidence by alias kind. A cashtag is close to unambiguous; a bare brand
# token is where every false positive in the labelled set came from.
_BASE_BY_KIND = {"cashtag": 0.95, "legal": 0.70, "informal": 0.55, "brand": 0.40}

# Score at or above which a match is written with issuer_id set.
ACCEPT_THRESHOLD = 0.70
# Below ACCEPT but at or above this, the row is kept with needs_review = true.
REVIEW_THRESHOLD = 0.45


@dataclass(frozen=True)
class AliasRow:
    id: int
    issuer_id: int
    normalized_alias: str
    kind: str


@dataclass(frozen=True)
class MatchResult:
    issuer_id: int
    alias_id: int
    confidence: float
    needs_review: bool
    reasons: tuple[str, ...]


def normalize_text(text: str) -> str:
    """Lowercased, punctuation-flattened, cashtags preserved."""
    lowered = text.lower()
    # HTML entities survive the adapter's excerpt; they would otherwise glue
    # tokens together and break word boundaries.
    lowered = re.sub(r"&#x?[0-9a-f]+;|&\w+;", " ", lowered)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$ ]+", " ", lowered)).strip()


def _occurs(alias: str, haystack: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", haystack) is not None


def score(alias: AliasRow, title: str, body: str) -> tuple[float, list[str]]:
    normalized_title = normalize_text(title or "")
    normalized_body = normalize_text(body or "")
    combined = f"{normalized_title} {normalized_body}".strip()

    if not _occurs(alias.normalized_alias, combined):
        return 0.0, []

    reasons: list[str] = [f"kind={alias.kind}"]
    confidence = _BASE_BY_KIND.get(alias.kind, 0.40)

    tokens = alias.normalized_alias.split()
    if len(tokens) > 1:
        # Multi-word names are far more specific: "sb energy" collides with
        # almost nothing, "advance" collides with English.
        bonus = 0.08 * (len(tokens) - 1)
        confidence += bonus
        reasons.append(f"tokens={len(tokens)}+{bonus:.2f}")

    # Checked per token, not on the whole alias string. Testing the joined
    # string let "fast track" (Fast Track Group) and "ai strategy" (AI STRATEGY
    # INC.) through untouched -- and worse, the multi-token bonus above rewarded
    # them for being *specific*. One of those two aliases produced 1,157 of
    # 3,613 accepted matches across 90 days of Hacker News.
    # An alias needs at least one token that is both uncommon AND long enough
    # to carry information. Counting common tokens alone is not enough: "ai
    # strategy" has one uncommon token ("ai"), but a two-letter abbreviation is
    # not evidence of anything, and AI STRATEGY INC. produced 1,157 of 3,613
    # accepted matches across 90 days on the strength of it.
    distinctive = [t for t in tokens if t not in COMMON_WORDS and len(t) >= 4]
    common_tokens = sum(t in COMMON_WORDS for t in tokens)

    if not distinctive:
        # The whole alias is ordinary language. The penalty must exceed the
        # multi-token bonus above, or a longer common phrase outscores a
        # shorter one purely for being longer.
        penalty = 0.40 + 0.08 * (len(tokens) - 1)
        confidence -= penalty
        reasons.append(f"no_distinctive_token-{penalty:.2f}")
    elif common_tokens:
        # Partly ordinary: "smart pointer group" is riskier than "spinnova"
        # but safer than "fast track".
        penalty = 0.15 * common_tokens
        confidence -= penalty
        reasons.append(f"common_tokens={common_tokens}-{penalty:.2f}")

    if len(tokens) == 1 and len(alias.normalized_alias) <= 3:
        # Three characters is too little to be evidence of anything, whatever
        # kind it is.
        confidence -= 0.25
        reasons.append("tiny_alias-0.25")
    elif len(tokens) == 1 and alias.kind == "brand":
        # Brand tokens are heuristic extractions from a legal name, and every
        # false-positive family in the labelled set came from one: "laser",
        # "advance", "aura", "devonian". A single-token legal name, by
        # contrast, IS the company's name -- penalising "Oura" the same way
        # cost two true positives for no precision gain.
        confidence -= 0.20
        reasons.append("single_token_brand-0.20")

    if _FINANCIAL_CONTEXT.search(combined):
        confidence += 0.20
        reasons.append("financial_context+0.20")

    if _occurs(alias.normalized_alias, normalized_title):
        confidence += 0.08
        reasons.append("in_title+0.08")

    return max(0.0, min(1.0, confidence)), reasons


class AliasIndex:
    """n-gram lookup over the alias table.

    `match` originally scored every alias against every item, one regex each.
    At 1,731 aliases that is ~1,700 regex searches per item -- 8.7 million for a
    5,000-item batch, which took minutes. This screens each item in time
    proportional to its own length instead: tokenise, generate n-grams up to the
    longest alias, and look them up.
    """

    def __init__(self, aliases: list[AliasRow]) -> None:
        self._by_text: dict[str, list[AliasRow]] = {}
        for alias in aliases:
            self._by_text.setdefault(alias.normalized_alias, []).append(alias)
        self._longest = max((len(a.normalized_alias.split()) for a in aliases), default=1)

    def candidates(self, title: str, body: str) -> list[AliasRow]:
        tokens = normalize_text(f"{title or ''} {body or ''}").split()
        found: list[AliasRow] = []
        for size in range(1, self._longest + 1):
            for start in range(len(tokens) - size + 1):
                rows = self._by_text.get(" ".join(tokens[start : start + size]))
                if rows:
                    found.extend(rows)
        return found


def match(
    aliases: list[AliasRow] | AliasIndex, title: str, body: str
) -> MatchResult | None:
    """Best candidate above the review floor, or None.

    Returning None is the common case and the correct one: 99.7% of a real
    Hacker News window mentions no issuer at all. Storing those would be
    storing the internet.
    """
    # An index narrows to plausible aliases first; a bare list is the slow path
    # kept for tests and small alias sets.
    if isinstance(aliases, AliasIndex):
        aliases = aliases.candidates(title, body)

    best: MatchResult | None = None
    for alias in aliases:
        confidence, reasons = score(alias, title, body)
        if confidence < REVIEW_THRESHOLD:
            continue
        candidate = MatchResult(
            issuer_id=alias.issuer_id,
            alias_id=alias.id,
            confidence=round(confidence, 2),
            needs_review=confidence < ACCEPT_THRESHOLD,
            reasons=tuple(reasons),
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best
