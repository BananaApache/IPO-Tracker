# Daily attention panel

Reproduces the design in
[Reddit-Sentiment-Analysis-for-Tech-Stock-Prediction](https://github.com/DaltonPayne/Reddit-Sentiment-Analysis-for-Tech-Stock-Prediction):
daily social volume against daily stock returns, on densely-discussed listed
companies.

**That repository reports no results.** No R², no p-values, no correlations, no
sample sizes, no date ranges — it builds a pipeline (HuggingFace sentiment →
linear regression → heatmaps over Apple, Google, Microsoft, Amazon) and
publishes no finding. So there is no measured effect in it to reproduce; what is
reproducible is the method.

## Why a panel, and not the event study

The event study gives each listing **one** attention observation, so n is the
number of listings: 188, of which 6 had any attention at all. A panel gives one
observation per issuer-day, so n is issuers × days. Same data, two orders of
magnitude more power.

## Result: a null, this time well powered

`uv run python -m backend.panel`

| specification | n | Spearman ρ | permutation p |
|---|---|---|---|
| daily mention count → next-day return | **1,548** | **+0.037** | 0.148 |
| daily engagement sum → next-day return | 1,548 | −0.019 | 0.449 |

57 issuers. Per-issuer correlations run from −0.21 (Carnival) to +0.24 (Fastly),
with signs split roughly evenly — 11 positive, 8 negative among issuers with 20+
panel days. A pooled ρ near zero with mixed per-issuer signs is what noise looks
like.

**This null carries weight the event-study null did not.** At n=1,548 a |ρ| of
0.037 is a real measurement of "no relationship", not an absence of data. The
event study's null was uninformative — 96% of its sample had zero attention.

Specification detail that matters: mentions on day *D* are paired with the return
from *D* to *D+1*. Same-day mentions may be *reacting* to that day's move, so
pairing them with the same day's return would be look-ahead. The forward return
is the honest target.

## What this measures, and what it does not

It measures **volume**, not tone. A company discussed 200 times because it
collapsed and one discussed 200 times because it beat earnings are identical
here. Sentiment could carry signal that volume cannot, and that is the one
genuine reason to add it.

Adding it needs a dependency, which brief §7 requires asking about:

| option | cost | note |
|---|---|---|
| VADER (`vaderSentiment`) | small, pure Python, no models | lexicon tuned for social text; what most Reddit studies use |
| HuggingFace `transformers` | large — pulls torch | what the source repo uses; heavy for a container |
| hand-rolled finance lexicon | none | worse than VADER and more code to defend |

## The pre-IPO version is not reproducible

This is the part worth being blunt about. The panel works because the
densely-discussed issuers are **already-listed** names — Alphabet, Walmart,
GameStop, Southern, Nasdaq, Booking, Xerox. That is the same population the
source repo targets, arrived at independently.

Attention on the IPO cohort:

| cohort | issuers with mentions | mentions | share |
|---|---|---|---|
| `re_listing` (already reporting) | 53 | 9,106 | **78%** |
| `operating` (the IPO cohort) | 33 | 1,898 | 16% |
| no 8-A | 7 | 715 | 6% |

And within the study cohort, only **6 of 188** listings have any mention within
±14 days of listing.

**Sentiment cannot fix that.** Sentiment is a transformation of mentions: the
tone of zero posts is undefined, not neutral. Scoring 6 observations more
precisely does not make them 60. The binding constraint on the IPO thesis is
attention *coverage*, and the only fix is a venue where pre-IPO companies are
actually discussed — which is the Reddit finding already recorded in
[`docs/sources.md`](sources.md).
