"""Query strings for the watchlist, fixed before any counting happens.

The module brief requires the disambiguation rules to be written down before
collection rather than tuned after seeing counts, so they live here as data and
are read by every collector.

Why this file exists at all: the NYT Article Search key available to this
project cannot do fielded search. `fq=organizations:("Instacart")` returns 0
hits where the same term as `q` returns 17 (measured -- see
`data/source_probe.json`), so there is no entity field to constrain against and
the only precision lever is the phrase in `q` itself. That makes an
ordinary-word brand name unusable on its own: `q="Circle"` returns 124 hits for
June 2025, almost all of them the noun.

The rule, applied identically to every row:

  * If the brand name is not an ordinary English word and not shared with a
    better-known entity, the query is the brand name as a quoted phrase.
  * Otherwise the query is the quoted brand name AND a quoted qualifier that
    the company's own coverage would carry -- its corporate suffix where one is
    commonly printed ("Unity Software"), else its sector term ("Circle" +
    "stablecoin"). Implicit AND of two quoted phrases is supported and measured.

The qualifier is chosen from the company's registration and sector, never by
trying alternatives and keeping the one with the most hits. The cost is recall:
a narrowed query misses articles that name the company without the qualifier.
That trade is recorded per row in `watchlist.csv` as `query_form`, and the
`ambiguous` flag marks which rows paid it, so the analysis can report the
narrowed rows separately rather than pretending the two are comparable.
"""

# brand -> (nyt_query, twitter_query, why the brand needed narrowing)
#
# Twitter's query language does support field-ish operators and a much larger
# corpus, so its queries can afford to be broader than NYT's; they are still
# fixed here rather than per-run.
AMBIGUOUS: dict[str, tuple[str, str, str]] = {
    "Unity": ('"Unity Software"', '"Unity Software" OR "Unity Technologies"',
              "'unity' is an ordinary noun and a common headline word"),
    "Circle Internet": ('"Circle" "stablecoin"', '"Circle Internet" OR ("Circle" USDC)',
                        "'circle' is an ordinary noun"),
    "Chime": ('"Chime" "banking"', '"Chime" (banking OR neobank OR IPO)',
              "'chime' is an ordinary noun and a smart-doorbell brand"),
    "Arm Holdings": ('"Arm Holdings"', '"Arm Holdings" OR "$ARM"',
                     "'arm' is an ordinary noun"),
    "Figure": ('"Figure" "fintech"', '"Figure Technology" OR "Figure AI"',
               "'figure' is an ordinary noun; also collides with Figure AI"),
    "Bullish": ('"Bullish" "exchange"', '"Bullish exchange" OR "$BLSH"',
                "'bullish' is ordinary market vocabulary"),
    "Snowflake": ('"Snowflake" "software"', '"Snowflake" (cloud OR data OR "$SNOW")',
                  "'snowflake' is an ordinary noun and a political epithet"),
    "Slack": ('"Slack" "software"', '"Slack" (software OR app OR "$WORK")',
              "'slack' is an ordinary noun"),
    "Klarna": ('"Klarna" "payments"', '"Klarna" (payments OR BNPL OR IPO)',
               "'klarna' is a common Swedish verb; measured pulling Swedish sports copy"),
    "Bumble": ('"Bumble" "dating"', '"Bumble" (dating OR app OR "$BMBL")',
               "'bumble' is an ordinary verb"),
    "Zoom": ('"Zoom" "video"', '"Zoom" (videoconferencing OR "$ZM")',
             "'zoom' is an ordinary verb"),
    "Unitree Robotics": ('"Unitree"', '"Unitree"',
                         "unambiguous, but listed here to record it was checked"),
}


def query_for(brand: str) -> tuple[str, str, str, bool]:
    """(nyt_query, twitter_query, note, ambiguous) for one brand name.

    The default is the brand as a quoted phrase. A quoted phrase is used even
    for unambiguous names so that a multi-word brand is not silently split into
    an OR of its words by the provider.
    """
    if brand in AMBIGUOUS:
        nyt_q, tw_q, why = AMBIGUOUS[brand]
        return nyt_q, tw_q, why, brand != "Unitree Robotics"
    return f'"{brand}"', f'"{brand}"', "", False
