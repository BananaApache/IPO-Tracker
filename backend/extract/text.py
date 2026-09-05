"""HTML prospectus -> plain text, and locating the cover page inside it.

Kept separate from the extraction rules in prospectus.py so that the evaluation
set can be read with the same text these rules see, without the labelling being
influenced by the rules themselves.
"""

import html
import re

# Blank fields in SEC documents are padded with em/en spaces and figure spaces.
# These are not matched by \s in some contexts and survive a naive cleanup,
# which is how "$      and $      " turns into a pattern that looks like it has
# numbers in it.
_WIDE_SPACES = "             　\xa0"

_SCRIPT_STYLE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.I)
# Block-level closers become newlines BEFORE tags are stripped. Without this,
# adjacent table cells concatenate: a cover listing three banks in three <td>s
# yields "Goldman Sachs & Co. LLCJ.P. MorganBofA Securities".
_BLOCK = re.compile(r"(?i)</(td|th|tr|p|div|h[1-6]|li|table)>|<br\s*/?>")
_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.translate({ord(c): " " for c in _WIDE_SPACES})
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


# Phrases that appear on a prospectus cover and essentially nowhere else.
# Ordered by how strongly each implies "this is the cover".
_COVER_ANCHORS: tuple[tuple[str, int], ...] = (
    (r"the date of this prospectus is", 5),
    (r"initial public offering price", 5),
    (r"underwriting discounts? and commissions", 4),
    (r"proceeds,? before expenses,? to us", 4),
    (r"we are offering\b", 3),
    (r"per share\s*\n\s*total", 3),
    (r"book[- ]running manager", 3),
)


# A prospectus cover is at the front of the document. Anchors are searched only
# within this much of it, because every one of them also appears later: "initial
# public offering price" recurs in dilution and risk sections, and picking the
# strongest anchor globally put the window at character 232,025 on one filing
# whose cover sat at 1,534.
FRONT_MATTER_CHARS = 120_000


def find_cover(text: str, window: int = 20_000) -> tuple[str, int]:
    """Return (cover_text, offset). Falls back to the head of the document.

    Anchored on phrases rather than byte offsets -- in one sampled 424B4 the
    first occurrence of "underwriter" was at character 267,259 of 284,371, in a
    boilerplate "no underwriters were involved" sentence. But anchor strength
    alone is not enough: among strong anchors the EARLIEST wins, because a
    later occurrence of the same phrase is discussion of the offering rather
    than the offering itself.
    """
    front = text[:FRONT_MATTER_CHARS]
    best_offset, best_score = None, 0
    for pattern, weight in _COVER_ANCHORS:
        match = re.search(pattern, front, re.I)
        if not match:
            continue
        # Strictly stronger anchor, or equal strength but earlier in the file.
        if weight > best_score or (weight == best_score and match.start() < best_offset):
            best_offset, best_score = match.start(), weight

    if best_offset is None:
        return text[:window], 0

    # Include some text before the anchor: the offering sentence often precedes
    # the phrase that identifies the page.
    start = max(0, best_offset - window // 4)
    return text[start : start + window], start
