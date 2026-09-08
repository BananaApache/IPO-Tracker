# Pre-IPO attention analysis

Research module. **Separate from the deployed pipeline** — nothing here writes to
the Neon database, imports into `backend/`'s runtime path, or affects the API, the
worker, or the dashboard. Outputs are files under `research/data/`.

The deployed pipeline detects an issuer when it files an S-1, so a well-known
private company is invisible during the years when attention actually
accumulates. This module inverts that: start from companies that are known to
have listed, measure attention retrospectively over the window around
registration, and cross-reference it against post-listing price.

```bash
uv sync --group research     # pandas / pyarrow / matplotlib / jupyterlab
uv run --group research python -m research.collect.probe_sources      # measure the sources
uv run --group research python -m research.collect.finnhub_census     # Tier A census
uv run --group research python -m research.collect.edgar_enrich        # Tier B watchlist
uv run --group research python -m research.collect.edgar_events --offline   # free, no network
uv run --group research python -m research.collect.wikipedia --cohort tier_b  # free, keyless
uv run --group research python -m research.collect.nyt --priority 1    # 500/day
uv run --group research python -m research.collect.prices --priority 1
uv run --group research python -m research.collect.twitter --recompute # free; paid to collect
```

The research dependencies are a separate group. The deployed image runs
`uv sync --locked --no-dev`, which does not install them, so the API and worker
do not gain a pandas dependency for the sake of a notebook.

---

## What the sources actually deliver

Every source in this module advertises something its free tier does not
necessarily grant. `collect/probe_sources.py` asks each one a small number of
deliberately chosen questions and writes the answers, with a timestamp, to
`data/source_probe.json`. That file is the evidence behind this table. Rerun it
before trusting any of it — "the free tier reaches back 30 days" is a fact with a
shelf life.

Measured **2026-09-08**:

| source | advertised use here | what it actually did | verdict |
|---|---|---|---|
| **EDGAR submissions** | filing timeline | DRS / Form D / comment letters / withdrawals, all already downloaded | **in use, free** |
| **Wikipedia pageviews** | dense attention | keyless, monthly, absolute counts, **2015-07-01 → today** | **in use, free** |
| **Finnhub IPO calendar** | Tier A census | 2019-01 → today, month-windowed, full documented field set | **in use** |
| **Finnhub `stock/candle`** | price history | `HTTP 403 You don't have access to this resource` | **gated** |
| **Tiingo daily** | price history | as-traded daily bars for **all 39** Tier B tickers back to 2019; agrees with Polygon to **$0.0000** over 1,131 overlapping sessions | **in use, primary** |
| **Polygon daily aggs** | price history | works, but a **rolling 730-day** entitlement — older listings return `NOT_AUTHORIZED`, not an empty series | **in use, cross-check** |
| **NYT Article Search** | primary news signal | works and reaches 2019. Count is at `response.metadata.hits` | **in use** |
| **GNews** | corroborating news | `totalArticles` returned, but *articles stripped*: "historical data beyond 30 days is only available on paid plans" | **dropped** |
| **twitterapis.com** | social | no total-count field; paging runs newest-first. Date operators degrade badly on older windows | **in use, two fixed windows** |
| **redditapis.com** | social | no date-range parameter at all. With a *narrow* query a page spans ~600 days, enough for two fixed windows | **in use, two fixed windows** |

Three of these differ from what the module brief assumed, and each changed the
design rather than being worked around.

### NYT: the count moved, and `fq` does not work

The brief says to read `response.meta.hits`. There is no `response.meta` key in
the live envelope; the count is at **`response.metadata.hits`**.

More consequentially, the brief recommends `fq` with Lucene syntax for
precision. **Every fielded `fq` query tried returned 0 hits** —
`organizations:("Instacart")` returns 0 where the same term as `q` returns 17,
and `body:`/`headline:`/`lead_paragraph:` all behave the same way. A bare `fq` is
equivalent to `q`. So there is no entity field to constrain against and the only
precision lever is the phrase in `q` itself.

That matters because several watchlist names are ordinary English words.
Measured for June 2025: `q="Circle"` returns **124** hits, `q="Circle"
"stablecoin"` returns **5**. Implicit AND of two quoted phrases does work, and it
is the whole toolkit.

So the per-company query form is frozen in **`collect/aliases.py`** before any
counting, with the rule written out and the reason each narrowed name needed
narrowing. It is applied identically to every row and was not tuned after seeing
counts. The cost is recall — a narrowed query misses coverage that names the
company without the qualifier — and that cost is recorded per row as
`ambiguous` / `query_note` in `watchlist.csv` so the analysis can report narrowed
rows separately instead of pretending they are comparable to the rest.

### GNews: dropped, not half-used

The brief said to check the free tier's historical range before designing around
it, and to drop GNews rather than half-use it if the range fell short. It does:
**30 days**. A 2023 window returns `totalArticles: 24` with `articles: []` and an
explicit notice that historical articles were removed.

The count alone was available and was still rejected, for three reasons: it
cannot be reproduced offline or hand-checked because no article metadata comes
back; the totals are implausibly small for a global news index (64 articles for
Instacart across three months of 2021); and `q="Klarna"` was measured returning
Swedish-language sports coverage, because *klarna* is an ordinary Swedish verb,
with no language or precision control available to fix it. A number nobody can
audit is not a measurement.

### Reddit: dropped for the time series

`redditapis.com`'s search endpoint has **no `from`/`to` parameter** — only a
coarse `t=hour|day|week|month|year|all` bucket. A dated monthly window cannot be
expressed. Sorting by `new` and bucketing client-side does not rescue it: one
100-post page of `q=Instacart&sort=new` spanned **2.17 days**, and the service's
own response says Reddit "routinely stops serving a busy feed after a few
hundred items" and that a platform cut-off and a genuine end-of-data are
indistinguishable from the outside.

Reaching 2019 that way is not possible, so no Reddit series is presented. The
`sort=relevance` variant does return posts spanning 2020–2026, but relevance
ranking is not a census and cannot produce a monthly count.

### Prices: Finnhub is gated, Polygon is licensed but short

The brief says to confirm Finnhub can read historical OHLC before building the
return analysis and, if gated, to **stop and report rather than substitute a
scraped price source**. It is gated: `HTTP 403`.

No scraped source was substituted. Prices come from **Polygon**, the licensed,
keyed feed the deployed pipeline already uses for exactly this purpose and which
`docs/sources.md` chose over four unlicensed alternatives. Swapping one licensed
provider for another is not what that constraint forbids, and this module reuses
`backend.prices.polygon.PolygonClient` rather than writing a second one — so it
inherits `adjusted=false`, without which a corporate action would retroactively
rewrite a published return.

What the swap costs is history. The plan on this key grants a **rolling two-year
window**, measured at exactly 730 days. Consequences:

- Of 39 Tier B rows, **12** have a listing inside the entitlement and therefore
  can have a price panel at all. The other 27 get none.
- Those 12 are marked `price_available` in `watchlist.csv`. That column is a
  statement about the feed's entitlement, **not** about the company.
- The window slides. `edgar_enrich` therefore takes `--price-floor` as an
  argument rather than computing it from today, so re-running reproduces an
  earlier watchlist instead of silently reclassifying rows.
- Four of the 12 listed too recently to have 90 trading sessions yet (Jersey
  Mike's has 27). A 90-day return does not exist for them and is not imputed.

---

## The two sources that changed the analysis

Both are free. Neither was in the original module brief.

### DRS: the confidential filing, and why the S-1 is a leaky anchor

`collect/edgar_events.py` adds **no new provider**. Every filing it reads was
already downloaded by `edgar_enrich`, which used it for two dates and discarded
the rest.

Under the JOBS Act an emerging growth company files a **confidential draft
registration statement (DRS)** before its public S-1, and EDGAR exposes it only
once the company actually goes public. Measured across the watchlist:

| | days |
|---|---|
| DRS present | **39 of 40** companies that filed |
| median DRS → public S-1 | **96** |
| 75th / 90th percentile | 147 / 588 |
| maximum | **1,408** (Bullish) |

Two consequences.

1. **The public S-1 is a leaky event anchor.** "Attention before the S-1"
   silently includes however long the company had already been in registration —
   798 days for Reddit, 992 for Tempus. For **4 of 40** companies the DRS falls
   outside a 24-month pre-S-1 window entirely, so every month of their supposed
   pre-event baseline is already inside the registration process.
2. **It supports a sharper question.** The DRS was secret when filed; the S-1 was
   public. So: does attention rise around the confidential filing, or only the
   public one? Figure 2 can be anchored on either.

Note the asymmetry, because it decides how this may be described: DRS is
**useless to the deployed pipeline**, which detects issuers in real time and
cannot see a filing that is not public yet. It is available only retrospectively
— which is exactly what a retrospective module needs.

Also extracted, all previously unused: **Form D** (private placements, a pre-IPO
funding timeline), **CORRESP/UPLOAD** (SEC comment letters — registration
friction), **RW/AW** (actual withdrawals, which is what would make a "withdrawn"
comparison group evidence rather than a Finnhub status string), and amendment
counts.

### Wikipedia pageviews: the dense series, and the sample-size fix

Keyless, no per-call cost, no meaningful rate limit, monthly granularity in one
request per article, coverage from **2015-07-01** to today. It covers **all 40**
watchlist companies against NYT's quota-limited **12**, which is the real value:
the binding constraint on this module was never measurement precision, it was
sample size.

**This is not Google Trends.** `docs/google-trends-decision.md` rejected Trends
because it returns a *normalized* index (relative to its own query and window)
and is *sampled* (the same query twice gives different numbers). Pageviews are
absolute and deterministic, so they can be cited and recomputed. They do share
Trends' third property — an aggregate, not discrete events with ids and authors —
which is why this lives in `research/` and is never offered to the deployed
`SourceAdapter`.

Three data-integrity problems had to be solved. Each is measured, not assumed.

**1. Entity resolution.** Search resolves brands to the wrong subject often
enough that the mapping is audited in `data/wikipedia_resolution.csv`. Four
titles were wrong on the first pass — Robinhood resolved to a *Robin Hood*
disambiguation page, Peloton to the cycling term, Medline to the NIH
bibliographic database, and Palantir needed checking against the Tolkien object.
Two more were correctly named but nearly unvisited: `Reddit, Inc.` draws 3
views/month against 210,000 for `Reddit`, and `Chime Financial` has no traffic
against 11,000 for `Chime (company)`.

**2. Pageviews are keyed on the title string, not the article.** A renamed page
leaves its history behind. Zoom's current title holds 320,439 views; the old
`Zoom Video Communications` holds **9,659,520**. Without summing the article and
its redirects, Zoom's median month reads 16 views instead of ~23,000. Summing
also measures the right thing — attention to the entity, however the reader
navigated to it — and `views_by_title` keeps the merge auditable.

**3. Absent months are not zero months.** The API *omits* months before the
article existed. Zero-filling them would manufacture exactly the "attention
rises before the filing" shape being tested for, since an article created at IPO
time makes every earlier month look quiet. A subtler hazard: a title can be
**repurposed** — the `Bullish` article dates from 2005, when the title held the
market term, and was rewritten for the 2021 exchange; creation date cannot see
that. So each company also gets a floor from its own earliest EDGAR trace, and
only `views_valid` is gated while raw `views` is always retained. **1,391 of
5,076** company-months are excluded on this basis. Circle is the clearest case:
its raw peak of 45,790 falls to 8,066, because the earlier traffic belonged to a
previous occupant of its title.

The floor is deliberately conservative and can over-clip a long-established
foreign issuer whose first US filing is recent. That case and a repurposed title
are indistinguishable to any heuristic, so the few that matter are checked by
hand and recorded in `HAND_VERIFIED_FLOOR` — currently one entry, Klarna, with
its justification.

### What the two sources produced

Anchored on the DRS, with windows normalised to views per month and restricted to
the **20 of 36** companies having data in all three:

| window (relative to DRS) | median views/month | ratio to baseline | companies rising |
|---|---|---|---|
| baseline (−24 to −13 mo) | 21,260 | — | — |
| confidential (DRS → S-1) | 27,987 | 1.23× | 14/20, sign-test p = 0.115 |
| public (S-1 → +3 mo) | 54,195 | 1.76× | 18/20, sign-test p < 0.001 |

Read plainly: **the public filing is the attention event.** The confidential
period shows a modest rise that does not reach significance, so this does not
support a leakage story — and even if it had, elevated attention before filing is
equally consistent with a company simply becoming more famous, which is
plausibly *why* it filed. Separating those requires the withdrawn-company group,
which is not collected.

On returns, the analysis notebook reports a 30-session Spearman ρ near −0.66 with
an interval excluding zero — the Popularity Trap direction. **It is not a
finding.** n≈10, three horizons were tested with no correction, and the sign
reverses across them (30d −, 60d +, 90d −). A real price-drift effect does not
reverse twice in two months; sampling noise does. The notebook states this
arithmetically rather than leaving it to the reader.

---

## Cohort selection

### Tier A — census, every IPO, Finnhub only

`data/census.parquet`, built from `data/raw/finnhub_ipo/*.json` (93 monthly
windows, 2019-01-01 → today). This is the denominator: it says what the full
population looks like and lets the Tier B sample be compared against it.

**4,131 rows kept, 165 dropped.** Every drop is in `data/census_dropped.csv`
with its reason; all 165 were duplicates of a higher-information row for the same
symbol and date. Windows overlap at month edges and Finnhub restates a row as a
deal moves from `filed` to `priced`, so the de-duplication keeps the most
informative duplicate rather than the first — a `priced` row carries a final
price and share count that the earlier `filed` row does not.

| year | expected | filed | priced | withdrawn | total |
|---|---|---|---|---|---|
| 2019 | 0 | 52 | 216 | 58 | 326 |
| 2020 | 0 | 36 | 477 | 32 | 545 |
| 2021 | 1 | 127 | 1044 | 46 | 1218 |
| 2022 | 6 | 59 | 186 | 114 | 365 |
| 2023 | 7 | 55 | 162 | 52 | 276 |
| 2024 | 8 | 64 | 239 | 55 | 366 |
| 2025 | 8 | 144 | 371 | 35 | 558 |
| 2026 | 12 | 163 | 263 | 39 | 477 |
| **all** | **42** | **700** | **2958** | **431** | **4131** |

Two things worth noting. The 2021 SPAC boom is plainly visible at 1,044 priced
deals, four times the 2023 figure — which is exactly why cross-company overlay
in *calendar* time would mostly measure the era rather than the company. And the
brief's own working estimate of "~1,500 issuers" across the period is low by
roughly a factor of two against the 2,958 actually priced.

**Does the census actually look right?** A count that is silently truncated
looks exactly like a correct one, so it is checked against an independent
number rather than trusted. Splitting the priced rows with a crude SPAC
heuristic — a unit-style ticker, or "Acquisition" in the registrant name —
gives **596 SPAC-like and 448 operating** listings for 2021. The widely
reported figures for that record year are roughly 610 SPAC IPOs and 400
traditional ones. Two independent counts landing within a few percent is
reasonable evidence that the month-by-month paging captured the population
rather than a truncated slice of it.

The heuristic is approximate and is not used anywhere in the analysis — it
exists only for this check. Reproduce it from `census.parquet`.

The 431 `withdrawn` rows are companies that filed and never listed. They are kept
deliberately as the comparison group the module would otherwise have had to
hand-build. **They are not yet collected against** — see Limitations.

Counts are reproducible from the cached raw JSON with the network off:
`python -m research.collect.finnhub_census --normalize-only`.

### Tier B — deep collection, the watchlist

`data/watchlist.csv`, enriched by `collect/edgar_enrich.py` from the committed
seed at `data/companies_private_to_public_2019_2026.csv` (44 rows).

The seed is **not** treated as verified. Its `Year`/`Month` columns are
month-granularity and unsourced, so every row is resolved against EDGAR and the
Tier A census, and where they disagree the evidence goes in its own column with
`date_flag` recording the disagreement. A wrong seed value stays visible rather
than being laundered.

Resolved: **41 of 44 CIKs**, 39 rows in Tier B, 40 of 44 joined to a Tier A
census record on symbol.

**Five rows excluded**, each with a recorded reason:

| company | reason |
|---|---|
| Porsche AG | non-US listing (Frankfurt); not in the Finnhub US census |
| Unitree Robotics | non-US listing (Shanghai STAR); EDGAR has no registrant |
| DraftKings | SPAC merger: registers on S-4/proxy, so there is no S-1 anchor |
| Slack | no CIK resolved — full-text search returns 28 registrants, because Salesforce acquired it and `WORK` no longer exists in `company_tickers.json` |
| Medline | no CIK resolved — full-text search returns 9 candidate registrants |

The two unresolved CIKs are left unresolved on purpose. EDGAR full-text search
returned several plausible registrants for each and the first hit is not reliably
the right one; a wrong CIK would poison every date downstream, so the code
records the ambiguity instead of guessing.

**The listing date is not the 8-A date.** An early version of this used the 8-A
exchange-registration filing as the listing anchor and produced four apparent
seed disagreements. All four were the anchor's fault: an 8-A registers a class of
securities *before* trading starts, sometimes long before. Roblox filed its 8-A
on 2020-12-03 and first traded 2021-03-10 after switching from an IPO to a direct
listing; Coinbase's 8-A precedes its first trade by three weeks. With the anchor
corrected to the Finnhub `priced` date, **all 39 Tier B rows agree with the seed
month** and the 8-A is retained only as a lower bound. Where a price series
exists it overrides both — and for all 12 companies with one, the first traded
session matched the calendar date exactly (`data/price_reconciliation.csv`).

**How biased, in numbers.** Figure 0 in the analysis notebook is this comparison,
and the deal-size dimension is the sharpest version of it:

| deal size | Tier A (priced) | Tier B (watchlist) |
|---|---|---|
| <$50M | 681 (23.2%) | 0 (0%) |
| $50–200M | 1,143 (39.0%) | 0 (0%) |
| $200M–1B | 1,002 (34.2%) | 12 (31.6%) |
| >$1B | 107 (3.6%) | 26 (68.4%) |

The watchlist is 68% billion-dollar deals against a population that is 3.6%
billion-dollar deals, and it contains **not one** company from the two smallest
buckets, which are 62% of all priced IPOs. A list of "companies everyone
remembers going public" is close to a list of the largest offerings. That is the
selection this module cannot escape, which is why it is measured and stated
rather than disclaimed.

A note on joining to the census: the join is on symbol **and** date, because
symbol alone is not unique. 52 symbols are duplicated among the priced rows —
SPAC shells recycle a ticker across successive vehicles years apart, so `AACIU`
is Armada Acquisition Corp I, II *and* III. A symbol-only join fans out and
silently double-counts.

**Collection priority.** 39 companies × ~27 months exceeds NYT's 500/day quota,
so `tier_b_priority` fixes the collection order before any counting:

- **Priority 1 (12 rows)** — listing inside the price entitlement, so the row can
  appear in *both* halves of the analysis. 374 company-months, which fits one
  day's NYT quota.
- **Priority 2 (27 rows)** — attention trajectory only, no price panel possible.

Collecting whole companies in a defensible order beats collecting fragments of
all of them. The criterion is data availability, decided in advance, and
independent of any count.

---

## What is collected

**Tier A:** one row per issuer — name, symbol, exchange, status, calendar date,
share count, and the price field exactly as returned. Finnhub's `price` is
sometimes a range string (`"18.00-20.00"`); it is parsed into `price_low`/
`price_high` *and* kept verbatim as `price_as_returned`, because a parsed
midpoint would destroy the evidence of which it was.

**Tier B, per company per month**, registration − 24 months → listing + 3 months:

- **NYT article count** — `response.metadata.hits`. Counting never paginates;
  `hits` is already the total and pagination is capped and would burn quota.
  The first page of docs arrives in the same response, so it is stored at no
  extra cost and is what makes a precision hand-check possible.
- **X post density** — tweets per day, paginated within a fixed page budget,
  stored with `reached_month_start` saying whether the month was measured
  completely or estimated from its tail. See below for why this is not a count.
- News and social counts live in **separate tables** and are never summed.

**Tier B price series:** daily OHLC from the first traded session through
listing + 160 calendar days, trimmed to 90 sessions on read via `in_90d_window`.
The series is stored rather than the derived return, so the return window can be
recomputed without re-calling the provider. Session 0 is the opening print.

### X is measured as a density, not a count

The service returns a page of ~20 tweets and a cursor, and **no total-count
field**, so volume has to be paginated out at roughly $0.0008 per page. The
first design capped pagination per company-month and flagged the result as a
censored lower bound. **Calibration showed that does not work**, and the reason
is worth recording:

results come back **newest-first from the `until:` boundary**, so a page budget
is spent at the top of the window and works downward. `"Figma"` with a ten-page
budget returned 200 tweets for July 2023 of which **zero** fell in July — the
budget was exhausted within hours of the 1 August boundary, because Figma is
discussed constantly. A "count" of 0 for the most-discussed design tool on the
platform is not a small number, it is a broken measurement, and no `censored`
flag makes it safe to plot next to a real one.

So the metric is **density: tweets per day over the span the budget reached.**
For a quiet month the budget exhausts the whole month and the density is the
true monthly rate. For a busy month it covers a few hours and the density is
estimated from those hours. Either way the number means the same thing and is
comparable across companies, which a censored count is not. Cost per
company-month becomes fixed and predictable rather than proportional to the
company's fame.

The residual bias is real and recorded: a busy month is sampled from its
**final hours** rather than uniformly, so a spike earlier in the month is
under-weighted. `--anchors N` splits the budget across N points inside the
month, which reduces but does not remove it. Every record carries
`reached_month_start`, and figures hatch the months that did not reach it, so a
complete measurement is never drawn as though it were an estimate or vice versa.

**And a floor, because the estimator has a domain.** Calibration exposed the
limit plainly. For a low-volume name the budget covers most or all of the month
and the density is solid — Chime measured 0.71–0.83 tweets/day at coverage
0.91–1.0, CoreWeave 0.94 → 2.6 → 5.9 across 2023–2024. For a firehose name the
budget covers a sliver: `"Circle"` reached **0.0002** of January 2024, and 80
tweets over that interval divides out to **10,971 tweets/day**. That is
arithmetic, not measurement.

So a month whose coverage falls below `MIN_COVERAGE` (0.20) is reported as
**right-censored**: `volume_exceeds_budget` is true, `in_window_tweets` is a
floor, and **no rate is claimed**. Figure 1 draws those months as a rug tick at
the axis rather than a bar, because plotting them at any height would invent the
number the collector declined to produce.

The threshold was chosen after seeing the calibration coverage distribution, and
that is deliberate and safe: it is a threshold on the **instrument's reach**,
fixed without reference to any attention-versus-return result. It does not touch
the outcome variable, and the months it excludes are excluded for being
unmeasurable rather than inconvenient.

The practical consequence, stated plainly: **this source yields a usable monthly
series for modestly-discussed companies and cannot yield one for heavily
discussed companies at any sane budget.** Paging newest-first with no total and
no sampling primitive is the reason. That is a property of the service, it is
measured rather than assumed, and it is not worked around.

One detail in the estimator that is easy to get wrong: with several anchors the
tweets come from **disjoint** sub-windows, so pooling them and dividing by the
outer span divides a partial count by a full range and understates the rate. The
density is therefore computed as total observed tweets over total *observed*
span, with each anchor's covered interval accumulated separately and duplicate
ids counted once across anchors. `anchor_samples` in each raw record carries the
per-anchor `(tweets, span_days)` pairs so the arithmetic can be checked.

`until:` is not trusted as a filter either: the probe returned tweets stamped
2019-02-01 for a query bounded `until:2019-01-31`. Every tweet's `created_at` is
parsed and re-checked against the month client-side, duplicate ids across page
boundaries are counted once, and all timestamps are stored so span, density and
count are recomputable offline without re-billing.

`collect/twitter.py` **refuses to run without an explicit `--budget`**, because
every page is money. Use `--calibrate` to measure page cost on a spread of
months before committing to a full run; the sizing arithmetic is a cell in
`notebooks/01_collect.ipynb`.

### Two fixed windows, on a hard budget

The monthly density series was abandoned as unaffordable. What replaced it is
cheaper and answers a narrower question: how much IPO-specific chatter was there
in the **90 days before the public S-1** and the **90 days after the listing**?
Equal-length windows, so the two counts compare without normalising.

`collect/twitter_windows.py`. Three things make it affordable:

* **The query is narrow.** `"<brand>" IPO`, frozen for every company. Requiring
  the token `IPO` collapses the volume by orders of magnitude — bare `"Figma"` is
  a firehose of design-tool chatter, `"Figma" IPO` is the offering. It is also
  the right query for a pre-filing window: a tweet saying "Figma IPO" before the
  S-1 exists is anticipation.
* **Two windows, not 27 months.** 39 companies × 2 = 78 units of work.
* **A hard `--budget`,** checked before every request.

**Actual spend: 684 of 687 available calls ($0.55)** — 519 on the first pass
(77 of 78 windows; one errored and was correctly not cached) and 116 deepening
eight of them. A deepened window re-reads the pages it already bought, because
the earlier records predate the `final_cursor` field; that field is now stored,
so any future deepening resumes instead of re-paying.

#### The quality split, which matters more than the counts

There is no total-count field, so a count means paginating to exhaustion. Three
outcomes, and they mean different things:

| quality | n | meaning |
|---|---|---|
| `exact` | 29 | provider ran out inside the page cap — a real count |
| `lower_bound` | 35 | cap hit with results still in-window — a real floor |
| `unreliable_window` | 13 | cap hit but most results fell *outside* the window | 

The third category is the important one. Lyft's pre-filing window returned **0
in-window tweets out of 160 fetched** — the `since:`/`until:` operators were
largely ignored. **All 13 unreliable windows are pre-filing windows**, and
`post_listing` has none at all:

| window | exact | lower_bound | unreliable |
|---|---|---|---|
| pre_filing | 16 | 9 | **13** |
| post_listing | 8 | 31 | 0 |

So this instrument degrades on exactly the period the module most wants to
measure. Those rows are dropped, not averaged in.

#### Deepening, and the trap it set

168 calls were left after the first pass, so the eight `pre_filing` windows
nearest to exhausting were re-collected at a 20-page cap. Selection rule, fixed
in `deepen()` rather than chosen per run: *`pre_filing` windows classified
`lower_bound`, ordered by in-window count ascending.* Pre-filing is the side that
matters, and a window already returning many out-of-window results is near the
edge of the provider's data, so it is likeliest to exhaust. The rule naturally
puts a still-saturated window last, where the budget will not reach it.

**5 of 8 converted to exact.** Some were badly understated: Klarna went from
`≥100` to exactly **209**, Rivian from `≥102` to `≥360`. The original 8-page cap
was off by up to 3.5×.

That created a trap. Deepening one side of a pair gives it more search effort
than its partner, so Rivian now reads **360** pre-filing against **159**
post-listing — an inversion that measures the page budget, not the company.
Three pairs invert that way.

The fix is not to compare effort but to treat every count as an **interval**:

| observation | interval |
|---|---|
| `exact` c | `[c, c]` |
| `lower_bound` c | `[c, ∞)` |

"post exceeds pre" is established **iff** `post_low > pre_high`. This is looser
than an equal-effort rule in one direction — Arm Holdings' pre count is exactly
12 because it *exhausted* in 3 pages, so its partner's `≥160` settles the
direction whatever the page budgets were. And stricter in another: it refuses
Klarna, where pre is exactly 209 and post is only known to be `≥158`, so the true
value could sit on either side.

An earlier version of this keyed on equal page budgets and wrongly discarded
twelve perfectly sound pairs. `figures.build_windows_frame` carries the interval
logic and `research/tests` pins all four cases.

#### What it found

**20 of 25 usable pairs have direction established, and every one of them
rises.** Not a single company had more pre-filing than post-listing chatter.
Five are ambiguous (Bullish, Klarna, Palantir, Reddit, Rivian) — their intervals
overlap, so no direction follows either way.

Deepening earned its cost twice over: Bumble, Snowflake, Peloton and Birkenstock
moved from censored-on-both-sides (reading as flat, and uninformative) to
established; and Klarna, Rivian, Bullish and Palantir moved from *falsely* flat
to honestly ambiguous.

Among the **8 pairs where both counts are exact** — the only ones supporting a
magnitude — 8 of 8 rose, sign test **p = 0.008**, median ratio ~6×:

| company | pre-filing | post-listing |
|---|---|---|
| Firefly Aerospace | **0** | 101 |
| Tempus AI | **0** | 44 |
| Nu Holdings | 2 | 30 |
| Astera Labs | 8 | 105 |
| Fervo Energy | 8 | 56 |
| Rubrik | 20 | 113 |
| Mobileye | 46 | 131 |
| Jersey Mike's | 11 | 19 |

Two companies had *exactly zero* pre-filing posts matching the query. This
agrees with the Wikipedia result and by a much larger margin, and two
independent instruments pointing the same way is worth more than either alone.

Three caveats, none of them optional:

* **Only 8 of 25 pairs support a magnitude.** The other 17 are capped on at
  least one side; 12 still establish a direction by the interval test, and 5
  establish nothing at all.
* **This is not a mention count.** It counts posts matching one narrow query. A
  company discussed constantly without the token "IPO" scores zero, by design.
* **The pre-filing window is not a pre-event baseline.** It ends at the *public*
  S-1, and the median company filed confidentially ~96 days earlier, so most of
  it sits inside the confidential registration period. Each row records
  `window_start_days_after_drs`.

And the direction was never really in doubt: attention rising when a company
starts trading is close to a tautology. The interesting question was the
pre-filing run-up, and this instrument is weakest exactly there.

### Reddit: the same two windows, bucketed client-side

`collect/reddit_windows.py`. This source has the worst API of the seven and was
originally dropped outright; the narrow query is what made it viable.

**There is still no date range.** `q`, `sort`, and a `t` bucket relative to *now*
(`hour|day|week|month|year|all`). `t=year` means "the last twelve months", not
"the year around Lyft's 2019 S-1". So the windows are bucketed **client-side**:
sweep `t=all` sorted by `new`, page backwards with `after`, and assign each post
by its own `created_utc`.

That was hopeless before — a 100-post page of `q=Instacart` spanned **2.17
days**. With `"<brand>" IPO` a page spans **~600 days** (Lyft: page 1 covered
2024-12 to 2026-09, page 2 covered 2023-05 to 2024-12), so reaching a 2019
window costs about five pages. One sweep serves both windows, which makes this
cheaper per company than the X collection, where each window needed its own query.

**Spend: 101 of 270 calls ($0.20).** All 39 companies swept.

Two corrections applied to the raw response:

* **A brand filter.** The provider matches loosely — 18 of 100 raw Lyft results
  never mention Lyft. Every post is checked against the brand and its aliases
  before counting. **11% of everything fetched was discarded.**
* **Per-window completeness.** `sort=new` walks backwards from today, so the
  post-listing window is reached before the pre-filing one. A count is a count
  only if the sweep reached back past *that window's* start; otherwise it is a
  floor. An early version got this wrong at the sweep level and would have
  reported SpaceX's pre-filing count as **0** when the sweep had never gone back
  that far — a fabricated finding of no anticipation. `pre_filing_complete` and
  `post_listing_complete` are now separate.

#### 28 of 39 are incomplete, and more budget cannot fix them

Every incomplete sweep stopped because **Reddit stopped issuing a pagination
cursor**, not because it ran out of pages: 22 stopped at 3 pages, 5 at 1, 1 at 2,
against a cap of 7. Reddit's own response says a platform cut-off and a genuine
end-of-data are indistinguishable from outside. So **169 of 270 calls are left
unspent** — spending them could not change a single row.

The failure mode is the **mirror image** of the X windows. There, all 13
unreliable rows were *pre-filing* windows on *older* listings. Here the losses
are *recent, heavily-discussed* companies, because `sort=new` starts at today and
a busy query burns its cursor before reaching back. Two scrapers, two opposite
blind spots — a reason to report them separately rather than pooling them into
one "social" number.

#### What it found

Of the 11 companies with both windows complete, **11 of 11 rose**, sign test
**p = 0.001**:

| company | pre-filing | post-listing |
|---|---|---|
| StubHub | 1 | 59 |
| Chime | 10 | 41 |
| Mobileye | 9 | 35 |
| Cerebras Systems | 4 | 32 |
| Birkenstock | 5 | 31 |
| Circle Internet | **0** | 25 |
| Klaviyo | 5 | 24 |
| Firefly Aerospace | **0** | 23 |
| Duolingo | 1 | 18 |
| Rubrik | 1 | 15 |
| Arm Holdings | **0** | 13 |

Three companies had *exactly zero* pre-filing Reddit posts, and unlike the
partial sweeps these are real zeros — the sweep reached back past the window.

### Underpricing: a better-posed question, still underpowered

`collect/underpricing.py`. **Underpricing** — `(day-1 close − offer price) /
offer price` — is a far better dependent variable than the 30/90-day returns
elsewhere in this module: measured on day one so there is no window to choose,
and with decades of literature giving a benchmark (~15–20% in the US).

The design follows **Da, Engelberg & Gao (2011, *Journal of Finance*)**, who
found Google search volume predicts IPO first-day returns. Two choices come
from that paper:

* **Attention is measured strictly before the price is set.** The offer price is
  fixed the evening before the first trade, so the event window is days −30 to
  −1. Monthly pageviews cannot express that, so this uses **daily** granularity.
* **Abnormal attention, not level.** Raw views are dominated by fame. The
  regressor is the event window's mean daily views over the company's *own*
  baseline (days −120 to −31).

**Why it cannot be scaled to the census, which was the original hope.** Wikipedia
coverage is the wall: **0 of 20** randomly sampled recent IPOs have an article,
because Wikipedia's notability bar excludes most issuers. Google's search index
covers *every string*, including "Galaxy Payroll Group Ltd"; Wikipedia covers
notable subjects. That asymmetry is precisely why Da et al.'s design worked and
why this one is confined to the hand-picked watchlist. Search-based resolution
does not rescue it — it returns confidently wrong entities (SharonAI → *OpenAI*,
BKV Corp → *Devon Energy*, Legence → *VF Corporation*).

Prices come from **Tiingo**, which covers all 39 Tier B companies back to 2019
and agrees with Polygon to **$0.0000** across 1,131 overlapping sessions. Three
companies are excluded for having no pre-IPO baseline — Peloton, Astera Labs and
Tempus AI had Wikipedia articles created at or after their IPO, so no abnormal
ratio can be formed. That leaves **n = 35**.

#### The effect attenuated, and that is the finding

This section first ran at **n = 12**, when Polygon's 730-day wall was the binding
constraint. Tiingo took it to n = 35 and the estimate nearly halved:

| n | ρ | 95% CI | threshold |
|---|---|---|---|
| 12 | +0.517 | [−0.080, +0.841] | 0.576 |
| **35** | **+0.282** | [−0.057, +0.562] | 0.333 |

That is the most valuable thing this study produced, and it is a warning rather
than a result. The n=12 estimate was **inflated by small-sample noise** — the
most common way quantitative research goes wrong. Written up at n=12 with its
"ρ ≈ 0.52, just short of significance" framing, it would have been a false
positive dressed as near-evidence.

Full results at n = 35:

| relationship | Spearman ρ | 95% CI |
|---|---|---|
| abnormal attention → underpricing | **+0.282** | [−0.057, +0.562] |
| abnormal attention → opening pop | +0.315 | [−0.021, +0.587] |
| **raw** event views → underpricing | +0.219 | [−0.123, +0.515] |
| log(deal size) → underpricing | +0.111 | [−0.217, +0.416] |

Three observations survive the expansion:

1. **The direction is stable.** All three attention measures stay positive.
   Nothing reversed sign the way the 30/60/90-day returns in the earlier section
   did.
2. **The abnormal construction's advantage shrank, and an earlier revision of
   this README overstated it.** At n=12 abnormal beat raw 0.517 vs 0.105 — a 5×
   gap described here as "earning its keep". At n=35 it is 0.282 vs 0.219. That
   5× was also small-sample noise.
3. **The smaller estimate is the more credible one.** ρ ≈ 0.28 sits squarely in
   the published range for attention effects (0.1–0.3); 0.52 was implausibly
   large. Deal size correlates at only +0.11, so attention is not merely
   proxying for company size.

Median underpricing in this sample is **31.2%**, well above the US long-run
average — the watchlist bias again, since it is 68% billion-dollar deals.

**Status: a positive relationship of roughly literature-typical size, not
statistically distinguishable from zero at n = 35.** Detecting ρ = 0.28 at 80%
power needs **n ≈ 97**. Prices are no longer the constraint; the binding number
is how many Wikipedia-notable US listings can be curated, and expanding the
hand-assembled watchlist from 44 toward ~120 is the one move that would settle
it.

#### The free-tier price hunt, and why it is over

The n=12 ceiling is set entirely by the price feed, so four providers were tried
for a historical end-of-day close. All four fail, for four *different* reasons —
worth recording so none of them is retried.

| provider | free-tier verdict | net new companies |
|---|---|---|
| **Polygon** | 730-day entitlement, **account-wide** — `/v1/open-close`, `/v2/aggs/grouped` and `/v3/trades` all return `NOT_AUTHORIZED` for 2019 dates | baseline (12) |
| **Finnhub** | `/stock/candle` returns `403` on this key | 0 |
| **FMP** | Endpoint path is right (`/stable/historical-price-eod/full`; the legacy `/api/v3/historical-price-full` is closed to accounts created after 2025-08-31) and history reaches **5 years** — better than Polygon. But there is a **symbol allowlist**: only 8 of 39 watchlist tickers resolve (COIN, HOOD, PINS, PLTR, RBLX, RIVN, UBER, ZM), and 7 of those listed before the 5-year floor. The block is symbol-level across the whole API — even `/quote` refuses `RDDT` while `UBER` works on every endpoint | **+1** (Rivian) |
| **Alpha Vantage** | **No symbol restriction** — every ticker resolves. But `outputsize=full` and `TIME_SERIES_INTRADAY`'s historical `month` parameter are both premium, `compact` returns only the last 100 days, and `TIME_SERIES_WEEKLY` — which *is* free and unlimited — **systematically omits the IPO's first trading week**. Verified on 6 companies across Wednesday, Thursday and Friday listings: Figma's weekly series starts 2025-08-08 and its first bar maps onto Polygon's Aug 4–8, with the actual listing days (Jul 31, Aug 1) absent | **0** |

The demo key is worth a warning: `apikey=demo` *does* serve
`outputsize=full` (6,752 bars for IBM back to 1999), so a probe against `demo`
suggests the free tier works when it does not. Always verify entitlements with
the real key.

**Tiingo resolved it, on the free tier.** Documented, keyed, no symbol
restriction, and one call per ticker covers the whole window: 39 of 39 companies
collected back to 2019-03-29, zero failures. It satisfies the same §7 bar as
Polygon and Finnhub — a provider granting the use with an API key.

A fifth option was investigated and **rejected on licensing**: Dukascopy, via
`dukascopy-node`. Its own client library states it is *"not affiliated,
endorsed, or vetted by Dukascopy Bank SA"*, the binary `.bi5` feed needs no key
and grants no permission, and the format had to be reverse-engineered. That is
the same ground Yahoo Finance was rejected on in `docs/sources.md`. It also
covered only 6 of 39 tickers and quotes **CFD** prices rather than exchange
closes, which is the wrong number for underpricing regardless of licence.

One trap worth recording: Alpha Vantage's `apikey=demo` *does* serve
`outputsize=full` (6,752 IBM bars back to 1999), so probing with the demo key
makes the free tier look far more capable than it is. Always verify entitlements
with the real key.

**What would settle it is not a better attention measure.** It is a price feed
with real history: `census.parquet` already holds **705 priced IPOs with a clean
single offer price** inside the current entitlement, of which **392 are
operating companies** once SPACs are excluded (they price at $10 with ~0% pop by
construction). At n=392 the detectable effect drops from 0.53 to **0.10** —
inside literature range. The blocker is that Polygon's 730-day wall is
account-wide, not per-endpoint: `/v1/open-close`, `/v2/aggs/grouped` and
`/v3/trades` all return `NOT_AUTHORIZED` for older dates.

### Three instruments, one direction

| instrument | usable | rose | test |
|---|---|---|---|
| Wikipedia, DRS-anchored | 20 companies | 18/20 into the public window | p < 0.001 |
| X windows | 20 pairs with direction established | **20/20** | 8/8 exact, p = 0.008 |
| Reddit windows | 11 complete pairs | **11/11** | p = 0.001 |

Three sources with different corpora, different failure modes and different query
operators all say the listing is the attention event. That convergence is worth
more than any one of them.

It is also the least surprising possible result. Attention rising when a company
starts trading is close to tautological, and the question with real content — the
*pre-filing run-up* — is where every instrument here is weakest: Wikipedia's
confidential-window rise is not significant (p = 0.115), all 13 unreliable X rows
are pre-filing windows, and Reddit's losses concentrate in the companies with the
most pre-filing chatter. The withdrawn-company comparison group is what would
settle it, and it is still not collected.

### The monthly density series: abandoned, and why

`twitter_monthly_counts.parquet` does **not exist** and `data/raw/twitter/` is
empty. A previous prepaid balance was exhausted during development
(`HTTP 402 insufficient_credits`) across repeated calibration runs while the
density estimator was being corrected — the two-window collector above replaced
it precisely because it fits a small budget. `collect/twitter.py` remains
finished and correct if a large balance ever justifies the monthly series.

The density figures quoted above — Chime at 0.71–0.83/day, CoreWeave rising
0.94 → 2.6 → 5.9 across 2023–2024, `"Circle"` covering 0.0002 of January 2024 —
are real measurements taken during those calibration runs. Their payloads were
superseded by later re-collections and are not on disk, so **treat those numbers
as reported observations, not as reproducible artifacts**. Everything else in
this README is backed by a committed file.

Two lessons are now enforced in the code rather than left to discipline:

- **`--recompute` rebuilds every month's metrics from the stored timestamps with
  no network call and no cost.** Every tweet timestamp and covered interval is
  written to the raw record precisely so the estimator can be revised without
  re-billing. Re-collecting to fix an arithmetic bug spends money to obtain
  bytes already on disk, which is exactly how the balance went.
- **A month whose requests errored is never cached.** The cache is also the
  resume mechanism — a month on disk is never re-requested — so writing a failed
  month as "0 tweets, coverage 0" would make a later run skip it and read an API
  failure as a measured zero. A 402 now aborts the whole run and writes nothing.

## Limitations

Read these before quoting any number from this module.

1. **The Tier B watchlist is a biased sample by construction.** It was
   hand-assembled from well-known names — companies memorable enough to appear on
   a list of notable listings. It is not a random sample of IPOs and it
   over-represents large, heavily covered technology deals. Tier A exists so this
   bias can be *shown* rather than asserted; the first analysis figure is the
   comparison, not an appendix. **Do not promote a Tier B finding to a claim
   about "IPOs".** Any such claim must come from Tier A or be explicitly scoped
   to the watchlist.

2. **Survivorship is near-total.** Every Tier B row listed. The 431 `withdrawn`
   companies in Tier A are the intended comparison group and are **not yet
   collected against**, so as it stands the module can only describe companies
   that made it. Until that group is collected and anchored on filing date
   (they have no listing date), any statement about what distinguishes a
   successful listing is unsupported.

3. **The price panel is structurally absent before the listing date, and no
   proxy was substituted.** Roughly 24 of the 27 months in each window are
   pre-listing. That region is left blank and shaded "not publicly traded". It is
   not interpolated, backfilled, zero-filled, or replaced with a private
   valuation series. The blank is a fact about the company, not missing data.

4. **Price coverage is now complete; four companies still lack a full 90-day
   window.** Tiingo supplies all 39 Tier B companies back to 2019, superseding
   Polygon's 730-day limit. Four of the most recent listings have fewer than 90
   trading sessions simply because they have not traded that long yet, and no
   return is imputed for them.

5. **Social data covers two fixed windows, not a time series.** There is no
   monthly social panel; `collect/twitter.py` is complete but unrun. Figure 1's
   social panel is therefore absent, and Figure 4 (in an X and a Reddit variant)
   is the only social result.

6. **The X windows degrade on old listings, and are censored on busy ones.**
   All 13 `unreliable_window` rows are pre-filing windows — the provider's date
   operators were largely ignored there, so those counts are neither exact nor
   bounds. Of 25 usable pairs, only 8 support a magnitude, 12 more establish a
   direction by the interval test, and 5 establish nothing. And the counts are
   of one narrow query (`"<brand>" IPO`), not of all mentions. **The X call
   budget is now spent** (684 of 687), so the five ambiguous pairs cannot be
   resolved without more credits.

7. **Reddit reaches only 11 of 39 companies, and no budget can improve that.**
   Its endpoint has no date range, so windows are bucketed from a `sort=new`
   sweep; 28 of 39 sweeps ran out of pagination cursor before reaching the
   window, all of them at 3 pages or fewer against a cap of 7. 169 of 270 calls
   are deliberately unspent. 11% of fetched posts were discarded for never
   naming the company.

8. **When collected, monthly X data would be a density estimate, not a count.**
   `twitterapis.com` is a third-party service, not the official X API. Its
   coverage is not contractually guaranteed, its output may change shape or
   depth without notice, and reruns may not reproduce — which is why raw
   payloads and every timestamp are stored, not just derived counts. It is
   lower-trust than NYT/Finnhub/Polygon and is kept in its own tables; any
   figure using it says so. Most months are **tail-sampled**: the density is
   estimated from the month's final hours, not measured across it. Only months
   with `reached_month_start` are complete counts.

9. **The news signal is sparse.** Instacart's peak IPO month is 17 NYT articles;
   most pre-filing months are 0. A 27-point monthly series of mostly zeros
   supports very little. No trendline or smoothing is applied that would imply
   more resolution than exists.

10. **Underpowered by construction.** With ~12 companies in the priority-1 set
   and ~25 in the full watchlist, essentially every correlation between
   attention and return here is underpowered. The expectation is to label them
   that way, not to find a way not to. A null result is a real result.

11. **Ambiguous names traded recall for precision.** Rows flagged `ambiguous` in
   `watchlist.csv` use a narrowed query and are not directly comparable to
   unnarrowed rows. The rule was fixed before collection; the trade is real
   either way.

12. **`research/` is exempted from PROJECT_BRIEF.md §7's ban on third-party
   scraper APIs** by the module brief, and only for this module. Nothing from
   these sources feeds the deployed pipeline. Every other §7 constraint still
   applies here: read-only, no raw usernames persisted, licensed market data,
   rate limits respected in one place per source.

---

## Layout

```
research/
  collect/
    config.py           research-only settings; backend/config.py is untouched
    paths.py            every output path, in one place
    aliases.py          frozen per-company query forms — read before collecting
    probe_sources.py    measures what each source delivers -> source_probe.json
    finnhub_census.py   Tier A census
    edgar_enrich.py     seed CSV -> verified watchlist
    nyt.py              NYT monthly counts, resumable, daily-cap aware
    twitter.py          X monthly density; needs --budget, --recompute is free
    twitter_windows.py  X counts in two fixed windows; fits a small budget
    reddit_windows.py   Reddit counts, bucketed client-side (no date-range API)
    underpricing.py     daily pageviews + offer price -> first-day underpricing
    tiingo_prices.py    licensed daily bars for all 39, back to 2019
    edgar_events.py     DRS / Form D / comment letters / withdrawals -- free, offline
    wikipedia.py        pageviews: keyless, dense, 2015->today
    prices.py           Polygon daily bars + first-trade reconciliation
  data/
    companies_private_to_public_2019_2026.csv   the committed seed
    watchlist.csv                               enriched Tier B
    census.parquet / census_dropped.csv         Tier A + its exclusion ledger
    source_probe.json                           source capability measurements
    price_reconciliation.csv                    calendar date vs first trade
    edgar_events.parquet / edgar_filings.parquet   filing timeline
    wikipedia_monthly.parquet / wikipedia_resolution.csv
    twitter_windows.parquet                        before/after X counts
    reddit_windows.parquet                         before/after Reddit counts
    underpricing.parquet                           attention vs first-day pop
    tiingo_daily.parquet                           daily bars, all 39 companies
    nyt_monthly_counts.parquet
    twitter_monthly_counts.parquet
    prices_daily.parquet
    raw/                                        every provider payload, cached
    figures/                                    exact dataframe behind each figure
  notebooks/
    01_collect.ipynb
    02_analysis.ipynb
```

Every collector is **resumable and idempotent**: one cache file per unit of
work, and a unit already on disk is never re-requested. A run that dies at hour
six resumes rather than restarting. Every aggregation step reads only cached
files, so the whole dataset rebuilds with the network off.

Figures are written to `data/figures/*.parquet` before plotting, and plotting
code reads only those files — no live API call inside a cell that draws.

## Tests

```bash
uv run --group research python -m pytest research/tests -q
```

Under their own root, not `tests/`. `tests/conftest.py` refuses to run any
session against a non-local database — correctly, since the main suite reads
`.env` and would otherwise open transactions against production Neon. That guard
fires at `pytest_sessionstart` and so blocks the whole session regardless of
which test was selected. These tests touch no database, so they live separately
rather than weakening the guard or routinely overriding it.

They cover the pure functions only: month windowing, Finnhub's polymorphic
`price` field, the month arithmetic behind the collection windows, X timestamp
parsing (including the out-of-window case the probe found), and the frozen query
rules. The collectors themselves are exercised by running them; their
correctness rests on the cached payloads, not on a mock of a provider whose real
behaviour is the thing this module had to measure in the first place.

## What is committed, and what is not

Everything under `data/` is committed **except** `data/raw/edgar/`. That cache is
~7 MB, freely and deterministically re-fetchable from a public API, and it grows
whenever any watchlist company files anything — so committing it means a large,
noisy diff on every re-run for no reproducibility gain. What reproducibility
needs from it is in `watchlist.csv`, which holds every date and identifier the
enrichment derived.

The other raw payloads *are* committed on purpose. They are measurements with a
retrieval timestamp that a later run may not reproduce — a provider can change
its index, its entitlements, or its coverage without notice — which is exactly
why the counts have to be recomputable from the stored bytes.
