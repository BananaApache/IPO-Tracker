"""Matcher unit tests. The measured evaluation lives in tests/evaluate_matching.py."""

from backend.match.aliases import generate
from backend.match.matcher import ACCEPT_THRESHOLD, REVIEW_THRESHOLD, AliasRow, match, score

LEGAL = AliasRow(1, 1, "oura", "legal")
BRAND = AliasRow(2, 2, "laser", "brand")
MULTI = AliasRow(3, 3, "sb energy", "legal")
CASHTAG = AliasRow(4, 4, "$oura", "cashtag")


def test_common_words_are_not_emitted_as_brand_aliases():
    kinds = {(a.normalized_alias, a.kind) for a in generate("First Breach, Inc.")}
    assert ("first", "brand") not in kinds
    assert ("first breach", "legal") in kinds


def test_brand_alias_survives_when_it_is_not_an_ordinary_word():
    kinds = {(a.normalized_alias, a.kind) for a in generate("Electra Therapeutics, Inc.")}
    assert ("electra", "brand") in kinds


def test_single_token_legal_name_outscores_single_token_brand():
    """Oura is the company's name; laser is a heuristic extraction."""
    legal, _ = score(LEGAL, "Oura's rings found their way onto fingers", "")
    brand, _ = score(BRAND, "Household Laser Cuts", "")
    assert legal >= ACCEPT_THRESHOLD
    assert brand < REVIEW_THRESHOLD


def test_ordinary_word_company_names_are_suppressed():
    """Regression: real issuers are named Click Holdings, Track Group and
    Pattern Group. A hand-written stoplist missed all three, and every one was
    accepted at 0.70 against unrelated Hacker News posts."""
    for word in ("click", "track", "pattern", "gold", "flash"):
        alias = AliasRow(99, 99, word, "legal")
        confidence, reasons = score(alias, f"Show HN: a tool to {word} things", "")
        assert confidence < ACCEPT_THRESHOLD, f"{word} scored {confidence}"
        assert "common_word-0.40" in reasons


def test_financial_context_fires_on_normalized_text():
    """Regression: the pattern held 's-1' while normalize_text produces 's 1',
    so it could never match. Cost 3 of 5 true positives on the first run."""
    with_ctx, reasons = score(LEGAL, "Oura S-1", "")
    assert "financial_context+0.20" in reasons
    assert with_ctx > score(LEGAL, "Oura ring review", "")[0]


def test_multi_token_alias_scores_above_single_token():
    assert score(MULTI, "SB Energy raises", "")[0] > score(LEGAL, "Oura raises", "")[0]


def test_cashtag_is_near_certain():
    assert score(CASHTAG, "buying $OURA today", "")[0] >= 0.9


def test_word_boundaries_prevent_substring_bleed():
    assert score(LEGAL, "the ouroboros pattern", "")[0] == 0.0
    distinctive = AliasRow(7, 7, "spinnova", "brand")
    assert score(distinctive, "Spinnova fibre process", "")[0] > 0.0
    assert score(distinctive, "spinnovation is not a word", "")[0] == 0.0


def test_no_candidate_returns_none():
    assert match([LEGAL, BRAND, MULTI], "Rust async runtimes compared", "") is None


def test_review_band_is_flagged_not_accepted():
    borderline = AliasRow(9, 9, "bamboo", "brand")
    result = match([borderline], "Bamboo Insurance shares", "")
    if result is not None and result.confidence < ACCEPT_THRESHOLD:
        assert result.needs_review
