# Entity resolution: the precision/recall tradeoff

Linking social posts to pre-ticker issuers. The hard part is that a pre-IPO
company has no symbol to search for — only a name, and names are words.

## The problem, measured

40,000 Hacker News stories and comments over four days, matched against 62
issuers (126 aliases):

| | count | |
|---|---|---|
| items in corpus | 40,000 | |
| contain **any** alias token (word-bounded) | 116 | 0.29% |
| actually **about** an issuer | 5 | 0.0125% |

**Word-bounded substring matching is 4.3% precise** (5/116). That is the number
every design decision below is trying to beat, and it is why the brief forbids
naive substring matching.

The 111 false positives are not exotic. They are the ordinary use of ordinary
words:

| alias | issuer | what it actually matched |
|---|---|---|
| `laser` | Laser Photonics Corp | "Household Laser Cuts", "20-kilowatt laser" |
| `advance` | Advance JV Group Ltd | "in advance", "advance user" |
| `aura` | an Aura-branded issuer | "aura farming", Salesforce Aura, a Rust agent named Aura |
| `devonian` | a Devonian-branded issuer | the Lower Devonian geological period |
| `inflection` | Inflection Point Acquisition | "the real inflection point" |
| `grande` | Grande Group Ltd | "Rio Grande" |
| `tailored` | a Tailored-branded issuer | "a strategy tailored to" |

## Pipeline

**normalize → candidate generation → scored match → threshold.** Substring
containment is only the *candidate* step. Scoring is what separates "this string
appeared" from "this is about that company".

### Alias generation

Per issuer: the legal name, the legal name with its suffix stripped, a brand
token, and a cashtag if a ticker exists.

**Ordinary English words are never emitted as brand aliases.** `First Breach,
Inc.` would otherwise contribute the alias `first` and produce a candidate on
roughly a third of everything ever posted. The issuer stays reachable through its
legal alias (`first breach`), which is far safer.

That is a deliberate recall sacrifice: an issuer whose brand *is* a common word
can only be found by its full name. Measured, not assumed — and the direction is
chosen on purpose, because a missed mention is a gap while a wrong one silently
corrupts a hype score.

### Scoring

| signal | weight | why |
|---|---|---|
| kind = cashtag | 0.95 | `$OURA` is close to unambiguous |
| kind = legal | 0.70 | the company's actual name |
| kind = brand | 0.40 | a heuristic extraction; every FP family above came from one |
| each token beyond the first | +0.08 | "sb energy" collides with almost nothing; "advance" collides with English |
| alias is a common English word | −0.40 | |
| single token, ≤3 characters | −0.25 | too short to be evidence of anything |
| single-token **brand** alias | −0.20 | applies to `laser`, not to `Oura` — see below |
| financial context nearby | +0.20 | ipo, s-1, nasdaq, prospectus, shares, filing |
| appears in the title | +0.08 | titles are about the subject; bodies digress |

Thresholds: **accept ≥ 0.70**, **review 0.45–0.70**, discard below.

## Measured result

Labels were recorded by reading every item, before the matcher existed. The
candidate-bearing population was labelled *exhaustively* (all 116), so recall
over this corpus is exact rather than estimated, and 60 items were drawn from the
39,884 containing no alias token — because that is the distribution the matcher
actually runs against.

### Read this before the table

**There are 5 true positives in 40,000 items, all of them the same issuer
reached through the same alias.** Any precision or recall figure below is
computed over n=5. It is an existence proof that the scoring separates a real
company name from an ordinary word — it is not a classifier evaluation, and it
should not be quoted as one.

The number that carries actual weight is the baseline comparison:

| | precision |
|---|---|
| word-bounded substring matching | **0.043** (5 of 116) |
| scored matcher | 0 false positives in 40,000 items |

That gap is what justifies the whole design. The per-run figures:

| | precision | recall | F1 | |
|---|---|---|---|---|
| naive substring baseline | 0.043 | 1.000 | 0.083 | n=5 |
| **first run, no tuning** | 1.000 | 0.400 | 0.571 | n=5 |
| after fixing what it exposed | 1.000 | 1.000 | 1.000 | n=5 |

**The first run is the honest number.** It exposed two defects:

1. **A real bug.** `_FINANCIAL_CONTEXT` contained `s-1`, but it was applied to
   *normalized* text where punctuation is already flattened to `s 1`. The pattern
   could never fire. It cost three of five true positives — including an item
   titled literally "Oura S-1".
2. **A blunt penalty.** The short-alias penalty applied to every alias kind, so
   `Oura` (a real, distinctive company name) was penalised exactly like `laser`.
   Now single-token *brand* aliases are penalised and single-token *legal* names
   are not.

## How much of this should be believed

Not much, and specifically:

- **Five positives.** Perfect precision and recall over five items is not a
  strong claim. One relabelled item moves recall by 20 points.
- **Every positive is the same issuer via the same alias.** The set really
  measures "does `oura` outscore `laser`". It says nothing about cashtags,
  multi-token names, or issuers whose brand is a common word, because the corpus
  contained no examples.
- **The review band is untested.** After the fix, 0 of 178 items land in
  0.45–0.70. The mechanism works (exercised by hand against the API) but no real
  item has ever landed there.
- **Recall is exact only with respect to the alias table.** An item naming an
  issuer in a form no alias covers was never found by the substring net and so
  was never labelled. Unknown unknowns are not counted.

Separation is nonetheless wide: lowest true positive **0.78**, highest false
positive **0.28**, with the accept threshold at 0.70 sitting inside a 0.50 gap.
That gap is the reason the thresholds were not tuned further — there is nothing
in this corpus to tune against.

## What a bigger alias set did to it

The evaluation above ran against **126 aliases** from 62 issuers. A 150-day
EDGAR backfill grew that to **1,731 aliases from 733 issuers**, and the matcher
fell over:

| aliases | items the matcher would store, per 60k HN items |
|---|---|
| 126 | ~8 |
| 1,731 | **1,148** (1.9%) |

The cause was not scoring. It was the word list. `COMMON_WORDS` was ~500 words
written from imagination, and the new issuer set contains **Click Holdings
Ltd.**, **Track Group, Inc.** and **Pattern Group Inc.** — whose single-token
legal aliases are `click`, `track` and `pattern`. None were on the list, so the
−0.40 common-word penalty never fired and all three scored 0.70 and were
accepted. `gold`, `flash`, `research`, `american`, `mobile`, `blue` and `civil`
followed.

**No amount of evaluation on the n=5 set could have caught this.** That corpus
contained one issuer whose name is a distinctive word. The failure mode only
exists once the alias table contains companies named after ordinary nouns —
which is exactly the case the brief called out with "Circle" and "Figure", and
which was absent from the data until the backfill.

The fix was to stop writing the list by hand: it is now derived by intersecting
tokens appearing in ≥10 of 40,000 HN items with a system dictionary, then
committed. 5,606 words. That drops the store rate from 1,148 to **89** per 60k
items, and spares distinctive names — `oura`, `electra`, `spinnova` and `amaero`
are all absent from it.

The lesson generalises past this project: a hand-written stoplist is fitted to
the examples its author happened to think of, and its gaps are invisible until
the data contains something they did not imagine.

## What would strengthen it

A corpus containing a cashtag mention, a multi-token issuer name in the wild, and
an issuer whose brand is a common word. None occurred in four days of Hacker
News, which is itself a finding: **this corpus is nearly all negative**, and the
metric that matters in production is false positives per 10,000 items, not F1.

Current rate: **0 false positives in 40,000 items.**

## Judgement call: product conversation counts as a mention

"Oura's rings found their way onto fingers" is labelled a **true match**, and the
matcher accepts it at 0.78, even though nobody in that thread mentions an IPO,
an S-1, or a share price. This is a decision, not an oversight, and it is
arguable in both directions.

**The case for counting it.** The system's thesis is *attention versus
fundamentals*. Someone posting about Oura rings is attention on Oura Inc.
regardless of whether they know a registration statement exists — and for a
pre-IPO company, that is precisely the attention worth measuring. Retail interest
in a consumer product routinely precedes and predicts interest in its offering.
A metric that only counted people already discussing the S-1 would measure how
many people read EDGAR, which is a much smaller and far less interesting
population, and one that arrives *after* the signal this project exists to catch.

**The case against.** It makes the hype axis partly a proxy for consumer brand
awareness. A large consumer company with a famous product will always outscore a
quiet B2B issuer, whatever either one's offering looks like. That is a real bias
and it will show up in the gem rankings: a company nobody has heard of cannot
score high on hype no matter how good its fundamentals are.

**Why the first reading wins here.** The bias in the second argument is a
property of the world, not of the measurement. Consumer companies genuinely do
attract more attention, and "low hype, high quality" is *supposed* to surface the
quiet B2B issuer — that is what the Hidden Gems view is for. Excluding product
conversation would not remove the bias, it would just make the hype axis noisier
by discarding most of the real signal.

**How to revisit it without relabelling.** Every true positive carries an
`ipo_context` flag in `tests/fixtures/matching_labels.py` recording whether the
mention was financial or product talk. Narrowing the metric to IPO-context
mentions is a threshold and scoring change, not a relabelling exercise. In the
current set that would drop 2 of 5 positives.

## What happens to non-matches

An item with no candidate at all is **not stored**. 99.7% of a real window
mentions no issuer; keeping those would be keeping the internet.

"Unmatched mentions are kept" means something narrower: an item that *did*
produce a candidate but scored below the accept threshold is stored with
`needs_review = true` and surfaced at `GET /api/v1/review/queue`.

A human rejection clears `issuer_id` but **keeps the row**. That is what
`mentions.issuer_id` being nullable is for — a human-confirmed non-match is the
most valuable label this system ever produces, and deleting it would throw away
the only ground truth the pipeline generates.
