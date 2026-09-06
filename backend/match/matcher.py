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

    if alias.normalized_alias in COMMON_WORDS:
        confidence -= 0.40
        reasons.append("common_word-0.40")

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


def match(aliases: list[AliasRow], title: str, body: str) -> MatchResult | None:
    """Best candidate above the review floor, or None.

    Returning None is the common case and the correct one: 99.7% of a real
    Hacker News window mentions no issuer at all. Storing those would be
    storing the internet.
    """
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
