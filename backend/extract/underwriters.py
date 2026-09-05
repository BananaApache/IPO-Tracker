"""Known underwriter and placement-agent names.

A dictionary, not open-ended extraction. A prospectus cover is dense with
capitalised strings that are not banks -- the issuer, the exchange, counsel, the
auditor, the transfer agent -- so harvesting capitalised text has a terrible
precision floor. Matching against a known list trades recall for precision, and
for this project that is the right direction: a missing bank is a gap, a wrong
bank silently corrupts the Phase 4 quality tier.

METHODOLOGICAL NOTE: this list was written from general knowledge of the
underwriting market, not by reading the validation set. It is still not a blind
construction -- the author had read those filings by the time it was written --
so `docs/extraction-eval.md` reports dictionary coverage separately from
extraction accuracy, and treats the underwriter recall figure as optimistic.
"""

import re

# Canonical name -> extra surface forms. The canonical form is what gets stored.
KNOWN_UNDERWRITERS: dict[str, tuple[str, ...]] = {
    # Bulge bracket
    "Goldman Sachs & Co. LLC": ("goldman sachs", "goldman, sachs"),
    "Morgan Stanley": (),
    "J.P. Morgan": ("jp morgan", "j p morgan", "jpmorgan"),
    "BofA Securities": ("bofa", "merrill lynch", "bank of america securities"),
    "Citigroup": ("citigroup global markets",),
    "Barclays": (),
    "Deutsche Bank Securities": ("deutsche bank",),
    "UBS Investment Bank": ("ubs securities", "ubs"),
    "Credit Suisse": (),
    "Wells Fargo Securities": ("wells fargo",),
    "RBC Capital Markets": ("rbc",),
    "BNP PARIBAS": ("bnp paribas",),
    "HSBC": (),
    "Nomura": (),
    "Mizuho": ("mizuho securities",),
    "SMBC Nikko": (),
    "TD Securities": (),
    "TD Cowen": ("cowen",),
    # Large independents / middle market
    "Jefferies": (),
    "Evercore ISI": ("evercore",),
    "Lazard": (),
    "Moelis & Company": ("moelis",),
    "Houlihan Lokey": (),
    "Perella Weinberg Partners": ("perella weinberg",),
    "Rothschild & Co": ("rothschild",),
    "Allen & Company LLC": ("allen & company", "allen and company"),
    "Guggenheim Securities": ("guggenheim",),
    "Piper Sandler": ("piper jaffray",),
    "Raymond James": (),
    "Stifel": ("stifel nicolaus",),
    "William Blair": (),
    "Baird": ("robert w. baird", "robert w baird"),
    "Truist Securities": ("truist",),
    "KeyBanc Capital Markets": ("keybanc",),
    "Citizens Capital Markets": ("citizens jmp", "jmp securities"),
    "Canaccord Genuity": ("canaccord",),
    "Needham & Company": ("needham",),
    "Oppenheimer & Co.": ("oppenheimer",),
    "Leerink Partners": ("svb leerink", "leerink swann"),
    "BTIG": (),
    "Cantor Fitzgerald": ("cantor",),
    "B. Riley Securities": ("b riley", "b. riley"),
    "Roth Capital Partners": ("roth capital", "roth mkm"),
    "Craig-Hallum": ("craig hallum",),
    "Northland Capital Markets": ("northland securities",),
    "Lake Street Capital Markets": ("lake street",),
    "JonesTrading": ("jones trading",),
    "Robinhood": ("robinhood securities", "robinhood financial"),
    # Small-cap / IPO specialists
    "Chardan": ("chardan capital markets",),
    "EarlyBirdCapital, Inc.": ("earlybirdcapital", "early bird capital"),
    "ThinkEquity": ("think equity",),
    "H.C. Wainwright & Co.": ("hc wainwright", "h.c. wainwright", "wainwright"),
    "Maxim Group": ("maxim group llc",),
    "Aegis Capital Corp.": ("aegis capital",),
    "Kingswood": ("kingswood capital markets",),
    "EF Hutton": ("ef hutton", "kingswood, a division"),
    "Titan Partners Group": ("titan partners",),
    "A.G.P./Alliance Global Partners": ("a.g.p.", "alliance global partners"),
    "Univest Securities": ("univest",),
    "US Tiger Securities": ("us tiger", "tiger securities"),
    "Prime Number Capital": ("prime number",),
    "WestPark Capital": ("westpark",),
    "Revere Securities": ("revere",),
    "Spartan Capital Securities": ("spartan capital",),
    "Dominari Securities": ("dominari",),
    "LifeSci Capital": ("lifesci",),
    "Rabo Securities": ("rabobank", "rabo securities"),
    "Benchmark Company": ("the benchmark company",),
    "Ladenburg Thalmann": ("ladenburg",),
    "ThinkPath": (),
    "American Trust Investment Services": ("american trust investment",),
    "Bancroft Capital": ("bancroft",),
    "Clear Street": ("clear street llc",),
    "Seaport Global Securities": ("seaport global",),
    "D. Boral Capital": ("d boral", "d. boral"),
}


def _norm(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_suffix(text: str) -> str:
    # Legal forms vary between filings for the same bank.
    return re.sub(
        r"\b(llc|inc|incorporated|corp|corporation|ltd|limited|lp|llp|plc|co|company|securities usa)\b",
        " ",
        text,
    ).strip()


# Longest surface form first, so "Goldman Sachs & Co. LLC" wins over "Goldman
# Sachs" and the canonical name is what gets stored.
_LOOKUP: list[tuple[str, str]] = sorted(
    (
        (_norm(surface), canonical)
        for canonical, aliases in KNOWN_UNDERWRITERS.items()
        for surface in (canonical, *aliases)
    ),
    key=lambda pair: -len(pair[0]),
)


def match_underwriter(candidate: str) -> str | None:
    """Canonical bank name for a cover-page line, or None."""
    normalized = _norm(candidate)
    if not normalized or len(normalized) < 3:
        return None
    bare = _strip_suffix(normalized)
    for surface, canonical in _LOOKUP:
        # Word-boundary containment: the cover line may carry a trailing
        # asterisk, a footnote marker, or a role suffix.
        if re.search(rf"(?<![a-z]){re.escape(surface)}(?![a-z])", normalized) or (
            bare and re.search(rf"(?<![a-z]){re.escape(surface)}(?![a-z])", bare)
        ):
            return canonical
    return None
