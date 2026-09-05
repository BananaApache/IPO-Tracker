"""Held-out validation set. Hand-labelled, never tuned against.

The 20-filing set in extraction_labels.py stopped being a test set the moment
three bugs were found by reading its failures -- it is a development set now.
These 12 filings were labelled the same way (by reading each cover) and the
extractor was run against them exactly once, with no change afterwards. No
issuer appears in both sets.
"""

LABELS: dict[str, dict] = {
    "h00_424B4_0002089447": dict(  # Advance JV Group -- self-underwritten direct offering
        instrument="share", price_low=None, price_high=None, price_final=2.00,
        price_disclosure="disclosed", note="fixed price $2.00/share; 'no underwriter has been engaged'",
        underwriters=[],
    ),
    "h01_424B4_0001892704": dict(  # First Breach -- prospectus supplement, already listed
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="Prospectus Supplement No. 1 attaching a quarterly report",
        underwriters=[],
    ),
    "h02_F-1_0002119287": dict(  # GLGHK -- FPI with a real printed range
        instrument="share", price_low=5.00, price_high=7.00, price_final=None,
        price_disclosure="disclosed", note="'will be between $5 and $7'",
        underwriters=["Pacific Century Securities, LLC"],
    ),
    "h03_F-1_0002113733": dict(  # Bogd FT -- FPI, bracket-bullet placeholders
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed", note="cover table reads '$ [*] $ [*]'",
        underwriters=["D. Boral Capital"],
    ),
    "h04_F-1A_0002099952": dict(  # Star Integratia -- FPI
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed",
        note="only an assumed US$7.00 midpoint for dilution math; no printed range located",
        underwriters=["EDDID SECURITIES USA INC."],
    ),
    "h05_F-1A_0002113605": dict(  # Chilwa Minerals -- 17k chars
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="amendment carries no prospectus",
        underwriters=[],
    ),
    "h06_S-1_0001527352": dict(  # Nexalin -- shelf base prospectus
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="shelf: prices set in future supplements",
        underwriters=[],
    ),
    "h07_S-1_0001972234": dict(  # VenHub Global -- resale
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="resale registration",
        underwriters=[],
    ),
    "h08_S-1_0002141406": dict(  # Accelevation -- large syndicate, no role headers
        instrument="share", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_yet_disclosed", note="'between $ and $ .'",
        underwriters=["Morgan Stanley", "J.P. Morgan", "Goldman Sachs & Co. LLC", "Barclays",
                      "BofA Securities", "Houlihan Lokey", "Baird", "William Blair",
                      "Piper Sandler", "Wolfe", "Nomura Alliance"],
    ),
    "h09_S-1A_0002135163": dict(  # Albatross Acquisition -- 8k chars
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="Part II-only amendment",
        underwriters=[],
    ),
    "h10_S-1A_0002112634": dict(  # Sensei Harbor -- penny-stock self-underwritten shell
        instrument="share", price_low=None, price_high=None, price_final=0.02,
        price_disclosure="disclosed",
        note="'at a fixed price of $0.02 per share'; par value is $0.001, separate",
        underwriters=[],
    ),
    "h11_S-1A_0002111838": dict(  # Haymaker Acquisition Corp V -- 8k chars
        instrument="other", price_low=None, price_high=None, price_final=None,
        price_disclosure="not_found", note="Part II-only amendment",
        underwriters=[],
    ),
}
