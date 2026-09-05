# Prospectus extraction: measured accuracy

Two hand-labelled sets, 32 filings total. **Labels were recorded by reading each
cover page, before the extraction rules existed.** Labelling with the code under
test would measure self-consistency and nothing else.

## Headline

| | Dev set (20) | **Held-out (12)** |
|---|---|---|
| Price disclosure class | 20/20 (100%) | **11/12 (92%)** |
| Price values exact | 20/20 (100%) | **11/12 (92%)** |
| Underwriter precision | 1.00 | **1.00** |
| Underwriter recall¹ | 1.00 | **1.00** |
| Underwriter recall, end-to-end² | 0.95 | **0.79** |

¹ over banks present in the dictionary  ² including banks the dictionary has never heard of

**The number to quote is the held-out one**, and the honest version of the story
is below, because the held-out figure moved during this work and it matters why.

## Why the dev-set numbers should be ignored

The 20-filing set stopped being a test set the moment I read its failures. Three
bugs were found and fixed that way, so 100% on it now measures how well the code
fits data it was tuned against. It is reported only to show the gap.

The 12-filing held-out set was labelled, then run **once**:

| Stage | Held-out price accuracy |
|---|---|
| First run, no tuning of any kind | **9/12 (75%)** |
| After fixing 2 general defects it exposed | 11/12 (92%) |

75% is the true generalisation estimate for the code as it stood. 92% is the
current code on a set that is no longer clean, and should be read as optimistic.
The two defects fixed were genuine and general, not label-fitting:

1. **Word order.** `offering price per Class A Ordinary Share will be between $5
   and $7` — the unit of measure precedes the figures. Patterns requiring
   "per share" *after* the numbers read this as no match at all.
2. **Cover location.** `find_cover` chose the strongest anchor globally, which
   put the window at character 232,025 on a filing whose cover was at 1,534.
   Anchors now resolve within front matter, earliest-strong-anchor wins.

Defect 2 was the third separate failure traced to the same root cause. The other
two showed up on the dev set: a window at 126,788 when the syndicate was printed
at 5,656, and a boilerplate "no underwriters were involved" sentence at 267,259
of 284,371 pulling the window to the wrong end of the document.

## Predicted vs measured

The pessimistic table in `docs/extraction-plan.md`, scored against reality:

| Field | Predicted | Measured | |
|---|---|---|---|
| price range on `S-1` | very low | very low | ✅ placeholders dominate |
| price range on `S-1/A` | moderate | very low | ❌ **worse than predicted** |
| final price on IPO `424B4` | good | mixed | ❌ most 424B4s are not IPOs |
| underwriters, large-cap | good | complete | ✅ 18/18 on Oura, 12/12 on Cumberland Farms |
| underwriters, small-cap `F-1` | poor | poor | ✅ dictionary misses obscure banks |

Two predictions were wrong in the same direction: I over-estimated how often a
document is an offering at a price at all. Across 67 live filings, **44 were
`not_found`** — resale registrations, rights offerings, shelf base prospectuses
and Part II-only amendments. Only 4 issuers ended with a price.

That is not a tuning failure. It is what the corpus is.

## The one remaining held-out failure

`h10 Sensei Harbor Corp.` — a shell offering 6,000,000 shares "at a fixed price
of **$0.02** per share". Rejected by `MIN_SHARE_PRICE = $1.00`.

Kept deliberately. The floor is what rejects par value, and par value was the
highest-confidence bare match on a dev-set filing (`$0.0001 per share`, read off
the capitalisation description). Lowering it to catch $0.02 shells would
reintroduce par-value false positives across the whole corpus, trading a
precision failure for a recall failure on exactly the issuers this project cares
least about. Recorded as a known false negative rather than tuned away.

## Underwriters

**Precision is 1.00 on both sets — zero false positives across 32 filings.** No
exchange, law firm, auditor or issuer name was ever matched as a bank. That is
the dictionary doing its job.

Recall splits into two different numbers and conflating them would flatter the
result:

- **1.00** over banks the dictionary contains — the matching logic never missed one.
- **0.79 held-out** end-to-end — because 3 of 14 labelled banks are not in the
  dictionary at all: `Pacific Century Securities, LLC`, `EDDID SECURITIES USA
  INC.`, `Wolfe`.

So underwriter recall is a **dictionary-coverage problem, not a matching
problem**. That is the intended failure direction: a missing bank is a gap, a
wrong bank silently corrupts a Phase 4 quality tier.

Caveat on the dictionary: it was written from general knowledge of the
underwriting market rather than from the label files, but I had already read
those filings by then, so its coverage is not blind. The end-to-end recall figure
is optimistic for that reason.

**Roles are almost never recovered.** Of 104 underwriter links written from live
data, **zero** carry `role = 'lead'`. Only 1 of 12 held-out filings printed a
role header at all in its front matter; large-cap covers list the syndicate as a
bare row of names. Names are stored with `role = NULL` rather than a guessed
`lead`, which is why `offering_underwriters.role` was made nullable in migration
`003`.

## Live corpus behaviour

One full pass over 67 filings, 37 seconds:

```
attempted=67  price=5  tbd=18  none=44  banks=104
```

| `extraction_method` | n | |
|---|---|---|
| `no_match` | 17 | no offering language found |
| `not_an_offering` | 14 | resale / rights offering |
| `to_be_negotiated` | 9 | price explicitly not yet set |
| `blank_placeholder` | 7 | `$ and $` on the cover |
| `rejected_per_unit` | 6 | unit offering, refused |
| `single_per_share` | 2 | price written |
| `assumed_price_only` | 1 | dilution-table placeholder |
| `cover_table` | 1 | price written |
| `range_between` | 1 | price written |

**A 7% price fill rate is the correct outcome**, not a bug to tune away. Every
one of the 44 `not_found` rows records *why* in `extraction_method`, so the gap
is auditable rather than mysterious.

## Reproducing

```bash
uv run python -m tests.evaluate_extraction            # both sets
uv run python -m tests.evaluate_extraction holdout    # held-out only
```

Labels: `tests/fixtures/extraction_labels.py` (dev),
`tests/fixtures/extraction_labels_holdout.py` (held-out).

## What would make this stronger

- The held-out set is contaminated now. A third set is needed before quoting a
  clean number again.
- 32 filings is small; a single label error moves a figure by 3 points.
- Both sets come from one 5-day window of EDGAR, so the mix of shells, SPACs and
  real IPOs reflects that window rather than the market.
