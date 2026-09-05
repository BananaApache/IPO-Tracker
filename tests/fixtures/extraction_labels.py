"""Hand-labelled validation set for prospectus extraction.

**These labels were recorded by reading the filings, not by running the
extractor.** Each cover page was opened and the offering terms read off it
before any extraction code existed. Labelling with the code under test would
measure only self-consistency.

20 filings pulled 2026-09-05, spanning S-1 (6), S-1/A (6), 424B4 (4), F-1 (2)
and F-1/A (2). The four F-1/F-1A rows are foreign private issuers.

Labelling decisions, applied consistently:

* `instrument` is what the cover actually offers. `unit` means share + warrant
  bundles (SPACs, small caps). A unit price is NOT a share price, so the correct
  extraction is to refuse rather than to store it.
* `underwriters` lists the syndicate banks named on the cover, including
  best-efforts *placement agents*. They are not underwriters in the firm-
  commitment sense, but the Phase 4 quality score asks "which bank is behind
  this deal", and a placement agent answers that.
* `price_disclosure`:
    - `disclosed`         a real offering price is printed
    - `not_yet_disclosed` the sentence exists with the figures left blank, or
                          the price is explicitly still to be negotiated, or the
                          only figure is an "assumed" price used for dilution
                          math
    - `not_found`         no offering price applies at all (resale
                          registrations, rights offerings, Part II-only
                          amendments)
"""

LABELS: dict[str, dict] = {
    "00_S-1_0002148245": dict(  # New Iceland Arctic Acquisition Corp. -- SPAC
        instrument="unit", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="12,500,000 units at $10.00 each",
        underwriters=["Chardan"],
    ),
    "01_S-1_0001802369": dict(  # ADARx Pharmaceuticals
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed", note="'between $ and $ per share'",
        underwriters=["J.P. Morgan", "Morgan Stanley", "TD Cowen",
                      "UBS Investment Bank", "LifeSci Capital"],
    ),
    "02_S-1_0002151558": dict(  # Legion Capital Acquisition Corp. -- SPAC
        instrument="unit", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="20,000,000 units at $10.00",
        underwriters=["BTIG"],
    ),
    "03_S-1_0001807887": dict(  # Laser Photonics
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed", note="price to be negotiated with placement agent",
        underwriters=["H.C. Wainwright & Co."],
    ),
    "04_S-1_0001787518": dict(  # T3 Defense -- resale
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="resale by selling stockholders; no offering price",
        underwriters=[],
    ),
    "05_S-1_0002133022": dict(  # Oura
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed", note="'between $ and $ per share'",
        underwriters=["Goldman Sachs & Co. LLC", "Morgan Stanley", "J.P. Morgan",
                      "Allen & Company LLC", "Jefferies", "BofA Securities", "Barclays",
                      "Wells Fargo Securities", "Citizens Capital Markets",
                      "KeyBanc Capital Markets", "Guggenheim Securities",
                      "Canaccord Genuity", "Needham & Company", "Raymond James",
                      "Rothschild & Co", "Truist Securities", "William Blair", "Robinhood"],
    ),
    "06_S-1A_0002142855": dict(  # PBT Land & Minerals -- rights offering
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="subscription rights offering",
        underwriters=[],
    ),
    "07_S-1A_0002111846": dict(  # Game Your Game -- resale
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="resale by selling stockholder",
        underwriters=[],
    ),
    "08_S-1A_0001115864": dict(  # ECOMINAS
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="no underwritten offering on the cover",
        underwriters=[],
    ),
    "09_S-1A_0001460702": dict(  # AIxCrypto -- VWAP equity line resale
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="resale of VWAP shares; no fixed price",
        underwriters=[],
    ),
    "10_S-1A_0002133037": dict(  # SB Energy -- Part II only
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="amendment contains Part II only; no prospectus",
        underwriters=[],
    ),
    "11_S-1A_0000840715": dict(  # CLEARONE
        instrument="unit", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="$3.50 per Unit (share + warrant)",
        underwriters=["ThinkEquity"],
    ),
    "12_424B4_0001671584": dict(  # Aptevo -- shelf resale
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="shelf/resale prospectus, already listed",
        underwriters=[],
    ),
    "13_424B4_0002004024": dict(  # Lianhe Sowell
        instrument="unit", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="$1.44 per Unit; the $4.00 nearby is a historical reference",
        underwriters=["R. F. Lafferty & Co., Inc."],
    ),
    "14_424B4_0002048271": dict(  # WeShop
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="not an underwritten cash offering",
        underwriters=[],
    ),
    "15_424B4_0002128462": dict(  # Three Lions Acquisition Corp. -- SPAC
        instrument="unit", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="10,000,000 units at $10.00",
        underwriters=["EarlyBirdCapital, Inc."],
    ),
    "16_F-1_0002080577": dict(  # LiPower New Energy -- FPI
        instrument="share", price_low=None, price_high=None, price_final=6.00,
        price_disclosure="disclosed", note="fixed $6.00 per share, $30,000,000 total",
        underwriters=["North America Securities LLC"],
    ),
    "17_F-1_0002027722": dict(  # Grande Group -- FPI, resale
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="resale registration for White Lion Capital",
        underwriters=[],
    ),
    "18_F-1A_0002094989": dict(  # RUI Holdings -- FPI, self-underwritten
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed",
        note="only an 'assumed offering price will be $1.00' used for dilution math; self-underwritten best efforts",
        underwriters=[],
    ),
    "19_F-1A_0002123232": dict(  # Cumberland Farms -- FPI
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed", note="'between $ and $ per ordinary share'",
        underwriters=["BofA Securities", "Goldman Sachs & Co. LLC", "Jefferies", "Barclays",
                      "J.P. Morgan", "Wells Fargo Securities", "Deutsche Bank Securities",
                      "UBS Investment Bank", "BNP PARIBAS", "Rabo Securities",
                      "TD Securities", "Raymond James"],
    ),
}
