"""Generating the surface forms an issuer gets referred to by.

One issuer, several aliases, each with a `kind` that the matcher scores
differently. The kinds are not decoration: a cashtag is near-unambiguous, a
suffix-stripped legal name is usually safe, and a bare brand token is where all
the false positives live.
"""

import re
from dataclasses import dataclass

from backend.match.common_words import COMMON_WORDS
from backend.normalize import normalize_company_name

# Tokens that carry no identifying information on their own. A brand alias made
# only of these is worthless.
_GENERIC = frozenset({
    "energy", "capital", "group", "holdings", "holding", "technologies",
    "technology", "systems", "solutions", "partners", "global", "international",
    "industries", "labs", "media", "health", "financial", "acquisition",
    "therapeutics", "pharmaceuticals", "pharma", "bio", "sciences", "resources",
    "securities", "ventures", "digital", "data", "cloud", "networks", "services",
})


@dataclass(frozen=True)
class Alias:
    alias: str
    normalized_alias: str
    kind: str  # legal | brand | cashtag | informal


def generate(legal_name: str, ticker: str | None = None) -> list[Alias]:
    out: dict[tuple[str, str], Alias] = {}

    def add(surface: str, kind: str) -> None:
        normalized = normalize_company_name(surface) if kind != "cashtag" else surface.lower()
        if not normalized:
            return
        out.setdefault((normalized, kind), Alias(surface.strip(), normalized, kind))

    # The name as filed, and the same name with its legal suffix removed.
    add(legal_name, "legal")
    stripped = normalize_company_name(legal_name)
    if stripped:
        add(stripped, "legal")

    tokens = stripped.split()

    # Brand: the leading token, which is what people actually say. Anything
    # longer drags in filler -- taking the run up to the first generic word
    # turned "LiPower New Energy" into "lipower new", which nobody writes.
    #
    # Skipped when it equals the legal form (same alias twice) or is under four
    # characters ("SB Energy" -> "sb" would match half the internet).
    if tokens:
        brand = tokens[0]
        # Ordinary English words are not emitted as brand aliases at all.
        # "First Breach, Inc." would otherwise contribute the alias "first" and
        # generate a candidate on roughly a third of everything ever posted.
        # The issuer stays reachable through its legal alias ("first breach"),
        # which is far safer. A deliberate recall sacrifice -- measured in
        # docs/matching.md rather than assumed away.
        if (
            brand != stripped
            and len(brand) >= 4
            and brand not in _GENERIC
            and brand not in COMMON_WORDS
        ):
            add(brand, "brand")

    if ticker:
        add(f"${ticker.upper()}", "cashtag")

    return list(out.values())


_UPSERT = """
    INSERT INTO aliases (issuer_id, alias, normalized_alias, kind)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (issuer_id, normalized_alias, kind) DO NOTHING
"""


async def rebuild_for_all(connection) -> int:
    """Regenerate aliases for every issuer. Idempotent -- the UNIQUE in
    migration 001 is what makes re-running safe."""
    written = 0
    for row in await connection.fetch("SELECT id, legal_name, ticker FROM issuers"):
        for alias in generate(row["legal_name"], row["ticker"]):
            status = await connection.execute(
                _UPSERT, row["id"], alias.alias, alias.normalized_alias, alias.kind
            )
            written += int(status.rsplit(" ", 1)[-1])
    return written
