# Wikipedia and IPO Underpricing

Part of the [attention study](../README.md). Tests one statement:

> **Companies that already had a Wikipedia article before going public are more
> underpriced.**

Underpricing is the first-day pop: a company sells shares at $20, the stock
closes at $25, and that 25% gap is money left on the table. The question is
whether firms already on Wikipedia before listing show a bigger pop than those
that weren't.

Everything here uses **free, unauthenticated data**. No WRDS, CRSP, Compustat,
SDC Platinum, or API keys.

---

## Notebooks

| Notebook | What it does |
|---|---|
| `01_build_panel.ipynb` | Data sources, universe screens, attrition accounting |
| `02_wikipedia_matching.ipynb` | The matcher, validation, human-review queue |
| `03_results.ipynb` | Difference in means, OLS with controls, robustness |

Supporting modules: `pipeline.py` (all fetch/parse/match logic),
`build_panel.py` (resumable crawler), `make_notebooks.py` (regenerates the
notebooks).

## Running it

```bash
uv run --with openpyxl python build_panel.py        # crawl (resumable, ~40 min)
uv run --with jupyter --with pandas --with statsmodels --with scipy \
       jupyter nbconvert --execute --inplace *.ipynb
```

Every network call is cached per firm under `../data/underpricing/cache/`, so
re-running only fetches what is missing. Code lives here; data lives with the
rest of the study's outputs under `research/data/underpricing/`.

## Data sources

| Need | Source | Cost |
|---|---|---|
| IPO universe, offer date, VC flag, founding year | Jay Ritter, `IPO-age.xlsx` | free |
| Underwriter reputation ranks (not used - see below) | Jay Ritter, `Underwriter-Rank.xls` | free |
| CIK resolution incl. renamed/delisted firms | SEC `cik-lookup-data.txt` (40 MB) | free |
| Offer price, shares offered, SIC | SEC EDGAR 424B prospectus | free |
| First-day close | Yahoo Finance chart API | free |
| Wikipedia existence, creation date, historical text | MediaWiki API | free |

## Design choices

| Choice | Why |
|---|---|
| Sample window 2014-2024 | Free price sources have purged firms that delisted out of older windows |
| Yahoo chart API for closes | Only free source with usable IPO-date prices |
| Ritter `IPO-age.xlsx` for the universe | Free, and carries offer date + VC flag |
| Name matching + redirects, with a review queue | Parent/subsidiary/product relationships are judgment calls |
| Controls: `vc`, `tech`, `log(proceeds)`, year FE | Underwriter reputation needs prospectus name-matching; no free news database spans the window |

## The matcher

A firm is coded `Wikipedia = 1` when an article about it existed before the
first trading day with **at least 30 words in the main body**. Redirects and
disambiguation pages count as zero.

Automating this naively produces a specific, damaging failure. An early pilot
missed Nubank, Revolve and Liberty Energy - all well-known firms - and pushed
them into the *control* group. Famous firms have larger first-day pops, so
those false negatives flipped the estimate's sign. The matcher therefore
returns **three** outcomes, not two:

- **accept** - high name overlap and the article reads as a company
- **reject** - no plausible company article
- **ambiguous** - routed to `../data/underpricing/out/review_queue.csv` for a human

The ambiguous class exists because of pairs like these, which no API can
separate:

| Query | Resolves to | Correct? |
|---|---|---|
| `Nu Holdings` | `Nubank` | yes - genuine parent/subsidiary, zero token overlap |
| `Array Technologies` | `ATI Technologies` | no - ATI held that name for four months in 1985, different company |

Two free signals settle most of the grey zone: **industry alignment** (the
filer's SIC description vs. the article's lead) and **already defunct** (an
entity acquired or dissolved well before the offer date cannot be the firm
going public). The rest go to a human.

### Validation

- **Benchmark cases: 6/6, zero wrong** - 4 auto-correct, 2 correctly escalated
- **Household-name recall: 16/18 (89%)** auto-coded (Uber, Airbnb, DoorDash,
  Reddit, Etsy, GoPro, ...); both misses escalated, not silently zeroed

## Results

**Wikipedia = 1: +22.63% mean underpricing (n=128). Wikipedia = 0: +17.08%
(n=401). Difference +5.55 pp, SE 3.26, t=1.70, p=0.091, 95% CI [-0.85,
+11.94].**

Stable across every treatment of the hard matches (+4.57 to +5.55 pp).

**The effect does not survive the controls** - VC backing and offer size
absorb the entire raw gap. Full discussion in [`../data/underpricing/out/RESULTS.md`](../data/underpricing/out/RESULTS.md).

## Known limitations

1. **Survivorship.** Firms that delisted are absent from free price data.
   Verified as genuine delistings, not lookup bugs (GlycoMimetics, CHC Group,
   EP Energy). The sample skews toward survivors.
2. **Look-ahead.** The no-split screen drops firms that later reverse-split,
   which are disproportionately poor performers.
3. **Incomplete controls.** No underwriter reputation, share overhang, or
   pre-IPO news count.
4. **Matcher attenuation.** Name-only matching misses some real articles,
   pushing the estimate toward zero.
5. **Underpowered.** n=529 against roughly 700 needed at this effect size, so
   a CI spanning zero is the expected outcome a fair fraction of the time even
   if the effect is real.
6. **Not causal.** This is a descriptive difference between two groups.
   Nothing here identifies what Wikipedia does.
