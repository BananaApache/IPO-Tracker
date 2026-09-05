"""Reading offering terms off a prospectus cover.

Every rule here is allowed to give up. A NULL means "we did not find it"; a
number means "a human could open the filing and see this". Nothing in between
gets written, because a wrong price at 0.4 confidence still reaches a chart.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from backend.extract.text import find_cover
from backend.extract.underwriters import match_underwriter

Disclosure = Literal["disclosed", "not_yet_disclosed", "not_found"]

# Plausibility bounds for a per-share offering price. The floor is what rejects
# par value: an S-1/A's most confident bare match was "$0.0001 per share", read
# off the capitalisation description. The ceiling rejects figures scraped out of
# aggregate-proceeds tables.
MIN_SHARE_PRICE = Decimal("1.00")
MAX_SHARE_PRICE = Decimal("500.00")

# A price figure must sit within this many characters of a phrase that says the
# number is an offering price. Proximity is doing as much work as the pattern:
# "$X per share" appears all over a prospectus, attached to warrants, prior
# rounds and conversion terms.
ANCHOR_PROXIMITY = 220

# How much of the document counts as front matter for syndicate detection. A
# prospectus cover is always near the front; the Underwriting section deeper in
# repeats the same banks, so overshooting costs little.
FRONT_MATTER_CHARS = 80_000

_PRICE_ANCHORS = re.compile(
    r"(?i)(initial\s+public\s+offering\s+price|public\s+offering\s+price"
    r"|price\s+to\s+the\s+public|offering\s+price\s+per"
    r"|at\s+a\s+fixed\s+price\s+of|fixed\s+price\s+of)"
)

# Anchored form: the range follows the anchor phrase, which may itself carry the
# "per <something> share" wording. Written separately from _RANGE because the
# unit of measure often precedes the figures -- "offering price per Class A
# Ordinary Share will be between $5 and $7" -- and a pattern that demands
# "per share" *after* the numbers reads that as no match at all.
_RANGE_AFTER_ANCHOR = re.compile(
    r"(?i)(?:initial\s+)?public\s+offering\s+price"
    r"(?:\s+per\s+[\w\s]{0,32}?share)?[^$\n]{0,80}?"
    r"\$\s*(\d[\d,]*\.?\d*)\s*(?:and|to|-|–|—)\s*\$?\s*(\d[\d,]*\.?\d*)"
)

# Documents that are not an offering at a price we could extract.
_NOT_AN_OFFERING = re.compile(
    r"(?i)(this\s+prospectus\s+relates\s+to\s+the\s+(?:offer\s+and\s+)?resale"
    r"|relates\s+to\s+the\s+resale\s+from\s+time\s+to\s+time"
    r"|subscription\s+rights\s+to\s+purchase)"
)

# Unit = share + warrant bundle. A unit price is not a share price, so it is
# refused outright rather than stored at reduced confidence.
_UNIT_OFFERING = re.compile(
    r"(?i)(each\s+unit\s+has\s+an\s+offering\s+price"
    r"|offering\s+price\s+per\s+unit"
    r"|units?\s+at\s+an\s+offering\s+price"
    r"|price\s+to\s+the\s+public\s+per\s+unit"
    r"|public\s+offering\s+price\s+of\s+\$[\d.,]+\s+per\s+unit)"
)

# "Assumed" prices are modelling placeholders used for dilution tables, not
# terms of the deal.
_ASSUMED = re.compile(r"(?i)assumed\s+(?:initial\s+)?(?:public\s+)?offering\s+price")

_TO_BE_NEGOTIATED = re.compile(
    r"(?i)offering\s+price[^.]{0,80}?will\s+be\s+determined\s+(?:through|by)\s+negotiation"
)

# The figures on a preliminary cover are left blank -- em-spaces, underscores or
# nothing at all between the dollar signs.
# "per share" may sit before the blanks ("offering price per share of common
# stock will be between $ and $ .") or after them, so it is not required here.
# What identifies the case is two dollar signs with nothing between them.
_BLANK_RANGE = re.compile(
    r"(?i)(?:initial\s+)?public\s+offering\s+price[^$]{0,160}?"
    r"\$\s*[_\s]*(?:and|to)\s*\$\s*[_\s]*(?:per\s|\.|,|\n)"
)

_RANGE = re.compile(
    r"(?i)\$\s*(\d[\d,]*\.?\d*)\s*(?:and|to|-|–|—)\s*\$?\s*(\d[\d,]*\.?\d*)\s*"
    r"per\s+(?:share|ordinary\s+share|class\s+a\s+ordinary\s+share)"
)

_SINGLE = re.compile(
    r"(?i)\$\s*(\d[\d,]*\.?\d*)\s*per\s+(?:share|ordinary\s+share|class\s+a\s+ordinary\s+share)"
)

# Cover price tables print the label and the figure on separate lines.
_TABLE_PRICE = re.compile(
    r"(?i)(?:initial\s+)?public\s+offering\s+price\s*\n?\s*\$?\s*\n?\s*(\d[\d,]*\.\d{2})"
)

_ROLE_HEADER = re.compile(
    r"(?i)(sole\s+book[-\s]?running\s+manager|joint\s+book[-\s]?running\s+managers?"
    r"|book[-\s]?running\s+managers?|lead\s+manager[s]?|sole\s+underwriter"
    r"|sole\s+placement\s+agent|placement\s+agent|co[-\s]?managers?|underwriters?)\b"
)

_COVER_END = re.compile(r"(?i)the\s+date\s+of\s+this\s+prospectus\s+is|prospectus\s+dated\s")


@dataclass(frozen=True)
class PriceResult:
    price_low: Decimal | None = None
    price_high: Decimal | None = None
    price_final: Decimal | None = None
    disclosure: Disclosure = "not_found"
    method: str = "no_match"
    confidence: float | None = None


@dataclass(frozen=True)
class UnderwriterResult:
    name: str
    role: str | None
    confidence: float


@dataclass(frozen=True)
class ProspectusResult:
    price: PriceResult = field(default_factory=PriceResult)
    underwriters: list[UnderwriterResult] = field(default_factory=list)


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _in_bounds(value: Decimal | None) -> bool:
    return value is not None and MIN_SHARE_PRICE <= value <= MAX_SHARE_PRICE


def _near_anchor(text: str, position: int) -> bool:
    # Looks both ways. The phrase that identifies a figure as an offering price
    # can follow it as easily as precede it ("at a fixed price of $2.00 per
    # Share for the duration of the offering. The offering price will ...").
    window = text[max(0, position - ANCHOR_PROXIMITY) : position + ANCHOR_PROXIMITY]
    return _PRICE_ANCHORS.search(window) is not None


def extract_price(text: str, cover: str) -> PriceResult:
    # Order matters. "Is this an offering at all?" precedes "what is the price",
    # because a resale prospectus is full of dollar figures that are not one.
    if _NOT_AN_OFFERING.search(text):
        return PriceResult(disclosure="not_found", method="not_an_offering")

    if _UNIT_OFFERING.search(text):
        # Refused, not downgraded: a unit price stored in price_final would
        # silently mean a different instrument downstream.
        return PriceResult(disclosure="not_found", method="rejected_per_unit")

    if _BLANK_RANGE.search(cover) or _BLANK_RANGE.search(text):
        return PriceResult(
            disclosure="not_yet_disclosed", method="blank_placeholder", confidence=0.95
        )

    if _TO_BE_NEGOTIATED.search(text):
        return PriceResult(
            disclosure="not_yet_disclosed", method="to_be_negotiated", confidence=0.9
        )

    for pattern in (_RANGE_AFTER_ANCHOR, _RANGE):
        for match in pattern.finditer(cover):
            low, high = _to_decimal(match.group(1)), _to_decimal(match.group(2))
            if _in_bounds(low) and _in_bounds(high) and low <= high:
                return PriceResult(
                    price_low=low, price_high=high, disclosure="disclosed",
                    method="range_between", confidence=0.9,
                )

    for match in _RANGE.finditer(cover):
        low, high = _to_decimal(match.group(1)), _to_decimal(match.group(2))
        if _in_bounds(low) and _in_bounds(high) and low <= high and _near_anchor(cover, match.start()):
            return PriceResult(
                price_low=low, price_high=high, disclosure="disclosed",
                method="range_between", confidence=0.9,
            )

    for pattern, method in ((_TABLE_PRICE, "cover_table"), (_SINGLE, "single_per_share")):
        for match in pattern.finditer(cover):
            value = _to_decimal(match.group(1))
            if _in_bounds(value) and (method == "cover_table" or _near_anchor(cover, match.start())):
                return PriceResult(
                    price_final=value, disclosure="disclosed",
                    method=method, confidence=0.85 if method == "cover_table" else 0.7,
                )

    # Checked last, and only against the cover. An "assumed offering price" is a
    # dilution-table placeholder and appears in almost every prospectus; letting
    # it run before the real patterns made one filing with a printed $6.00 cover
    # price report "not yet disclosed" because of a sentence 80,000 characters
    # further in.
    if _ASSUMED.search(cover) or _ASSUMED.search(text):
        return PriceResult(
            disclosure="not_yet_disclosed", method="assumed_price_only", confidence=0.85
        )

    return PriceResult(disclosure="not_found", method="no_match")


def _role_for(header: str) -> str | None:
    lowered = header.lower()
    if "co-manager" in lowered or "co manager" in lowered:
        return "co_manager"
    if "book" in lowered or "lead" in lowered or "sole" in lowered:
        return "lead"
    return None


def extract_underwriters(text: str, cover: str) -> list[UnderwriterResult]:
    found: dict[str, UnderwriterResult] = {}

    def record(name: str, role: str | None, confidence: float) -> None:
        existing = found.get(name)
        if existing is None or confidence > existing.confidence:
            found[name] = UnderwriterResult(name=name, role=role, confidence=confidence)

    # Region immediately above the prospectus date line -- where covers print
    # the syndicate.
    end = _COVER_END.search(text)
    blocks: list[tuple[str, str | None, float]] = []
    if end:
        blocks.append((text[max(0, end.start() - 900) : end.start()], None, 0.6))

    # Role headers are searched across the front of the document rather than
    # inside find_cover's window. The window is anchored on phrases, and on two
    # sampled filings the anchor matched a later occurrence -- putting the
    # window at character 126,788 while the syndicate was printed at 5,656.
    front = text[:FRONT_MATTER_CHARS]
    for header in _ROLE_HEADER.finditer(front):
        blocks.append((front[header.end() : header.end() + 400], _role_for(header.group(0)), 0.9))

    for block, role, confidence in blocks:
        for line in block.split("\n"):
            candidate = line.strip(" *|,;")
            if not candidate or len(candidate) > 60:
                # Long lines are prose, not a bank in a table cell.
                continue
            canonical = match_underwriter(candidate)
            if canonical:
                record(canonical, role, confidence)

    return sorted(found.values(), key=lambda u: (-u.confidence, u.name))


def extract(document_text: str) -> ProspectusResult:
    cover, _ = find_cover(document_text, window=20_000)
    return ProspectusResult(
        price=extract_price(document_text, cover),
        underwriters=extract_underwriters(document_text, cover),
    )
