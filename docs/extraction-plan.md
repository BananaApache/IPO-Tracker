# Prospectus extraction: plan, and what it will get wrong

**Status: proposed, not built.** Phase 2b. Written before implementation so the
design can be argued with rather than discovered in a diff.

Targets: **underwriters** (name + role) and the **price range** (low, high,
final), from the prospectus cover page of `S-1`, `S-1/A`, `F-1`, `F-1/A` and
`424B4`.

---

## Guiding rule

Same as Phase 3 matching: **normalize → locate → candidate generation → scored
match → threshold**. Below threshold, write nothing and record that extraction
was attempted and failed.

A NULL means "we did not find it." A number in `offerings.price_low` should mean
"a human could open the filing and see this." Nothing in between gets written.

---

## Pipeline

### 1. Get the right document

The daily index gives `edgar/data/{cik}/{accession}.txt` — the **full
submission**: every exhibit, every XBRL file, images inline as base64, all
concatenated with SGML wrappers. The one sampled was 964 KB and the primary
document was most of what mattered.

The submissions feed already fetched per CIK carries `primaryDocument` alongside
`accessionNumber`, so the direct URL costs **zero extra requests**:

```
https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{primaryDocument}
```

Measured sizes: 2.9 MB (S-1), 2.1 MB (S-1/A), 960 KB (424B4). Cap the download
and refuse anything absurd rather than streaming 50 MB into memory.

### 2. HTML to text

Regex-based tag stripping, no new dependency — verified adequate on all three
sampled filings. The one thing that must not be naive: **insert separators at
block boundaries** (`</td>`, `</tr>`, `</p>`, `</div>`, `<br>`) before stripping.
Underwriter names on a cover live in adjacent table cells, and stripping tags
without separators yields `Goldman Sachs & Co. LLCJ.P. Morgan`.

Also collapse ` `/` `/`\xa0`. SEC documents pad blank fields with
em-spaces, which survive a naive `\s` cleanup and corrupt the patterns below.

If this proves inadequate I would ask before adding `selectolax`.

### 3. Locate the cover page

Anchor on phrases, never on byte offsets. Candidate anchors: `The date of this
prospectus is`, `Per Share`, `Underwriting discounts and commissions`,
`We are offering`. Take a bounded window around the best anchor.

Offsets fail badly: in the sampled 424B4 the first occurrence of `underwriter`
was at character 267,259 of 284,371 — a boilerplate "no underwriters were
involved" sentence in the exemption disclosures, nowhere near a cover.

### 4. Price range

Pattern family, each with its own confidence weight:

| Pattern | Yields | Weight |
|---|---|---|
| `initial public offering price … between $X and $Y` | low, high | high |
| `$X to $Y per share` within the cover window | low, high | medium |
| `initial public offering price … $X per share` (424B4) | final | high |
| bare `$X per share` in the cover window | final | **low — see failures** |

Validation before any write: `0 < low ≤ high`, both under a sane ceiling, and
the figure must sit inside the cover window. Anything failing → NULL.

### 5. Underwriters

Names are matched against a **curated dictionary** of known underwriters rather
than harvested from capitalised text. The cover is dense with capitalised
non-banks (the issuer, the exchange, counsel, the auditor), so open-ended
extraction has a terrible precision floor.

Role comes from the nearest preceding header — `Book-Running Manager` /
`Lead Manager` → `lead`; `Co-Manager` → `co_manager`. No header found → store
the name with a lowered confidence and no role, or skip it.

`underwriters.normalized_name` is UNIQUE precisely so `Goldman Sachs & Co. LLC`
and `Goldman, Sachs & Co.` collapse to one bank instead of two tiers.

### 6. Storage — needs a migration

`offerings` has nowhere to record how a number was obtained. Migration `003`
would add:

```
offerings.extraction_confidence   NUMERIC(3,2)
offerings.extraction_method       TEXT          -- which pattern fired
offerings.extracted_at            TIMESTAMPTZ
offering_underwriters.confidence  NUMERIC(3,2)
```

Columns only, no new tables. Amendments **update** the existing offering rather
than inserting a second one; the natural key is the issuer, not the filing.

---

## What this will get wrong

Grounded in three real filings pulled on 2026-09-05, not speculation.

**1. Initial S-1s have no price range at all.** Oura's S-1 reads, literally:

> `initial public offering price per share of common stock will be between $ and $ .`

The figures are em-space placeholders. Expect the large majority of `S-1` rows
to yield NULL price — correct behaviour, not a failure, but it means the column
looks broken until amendments land. The danger is a loose regex wandering past
the blank and grabbing the next dollar figure in the document.

**2. Par value looks exactly like an offering price.** The sampled S-1/A's
highest-confidence bare match was `$0.0001 per share` — par value from the
capitalisation description. Any bare `$X per share` pattern must be
window-bounded and sanity-checked, and even then it stays low-confidence.

**3. Not every 424B4 is an IPO.** Already cost us one bug (see `a2ab444`). The
sampled 424B4 was a prospectus supplement from an already-listed Nasdaq company;
its only dollar figure was the prior day's closing price. Extraction must first
establish the document *is* an IPO prospectus — issuer has a tracked
registration, no pre-existing ticker, cover contains offering language — before
believing any number in it.

**4. Foreign private issuers are the hard tail.** Many `F-1` filers are small
issuers with non-standard covers, underwriters absent from any recognised tier
list, and occasionally non-USD figures. A dictionary approach will simply miss
them, which is the intended failure: a miss is recoverable, a wrong bank is not.

**5. Unit offerings.** SPACs and small caps price in *units* (share + warrant) at
`$10.00 per unit`. Different instrument, same-shaped sentence. Must be detected
and excluded, or `price_final` silently means something else.

**6. Covers without role headers.** Some list banks with no
`Book-Running Manager` label. Names extract; roles do not. Store name-only with
reduced confidence rather than guessing `lead`.

**7. Multiple offerings in one document.** Concurrent private placements and
selling-stockholder resales put a second price range on the page. First match
may be the wrong one.

**8. Bank name drift.** Handled by `normalized_name`, but the normaliser has to
survive `&`/`and`, `LLC`/`L.L.C.`, `Inc.`, and `Securities (USA)`.

---

## Expected yield

Deliberately pessimistic, to be checked against reality after the first run:

| Field | Expect populated |
|---|---|
| price range on `S-1` | very low — placeholders |
| price range on `S-1/A` | moderate |
| final price on IPO `424B4` | good, once non-IPO 424B4s are filtered out |
| underwriters on large-cap covers | good |
| underwriters on small-cap `F-1` | poor |

If measured yield beats this, the thresholds are too loose and are letting
guesses through. **A low fill rate is the honest outcome, not a bug to tune
away.**
