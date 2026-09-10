"""Generate the three analysis notebooks. Run once; edit the notebooks after."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


_N = [0]


def _cid():
    _N[0] += 1
    return f"cell{_N[0]:03d}"


def md(src):
    return {"cell_type": "markdown", "id": _cid(), "metadata": {},
            "source": src.strip("\n")}


def code(src):
    return {"cell_type": "code", "id": _cid(), "execution_count": None,
            "metadata": {}, "outputs": [], "source": src.strip("\n")}


def nb(cells):
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python", "version": "3.13"},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }


def write(name, cells):
    p = ROOT / name
    p.write_text(json.dumps(nb(cells), indent=1))
    print("wrote", p.name)


# ===========================================================================
# 01 - data sources and panel construction
# ===========================================================================
write("01_build_panel.ipynb", [
    md("""
# 01 — Building the IPO panel

**Question.** Are companies that already had a Wikipedia article before going
public *more underpriced* on their first trading day?

A hobby project, built with **only free, unauthenticated data sources**. No
WRDS, no CRSP, no Compustat, no SDC Platinum, no API keys.

### Design choices, and why

| Choice | Why |
|---|---|
| Sample window 2014–2024 | Free price sources have purged firms that delisted out of older windows |
| Yahoo Finance chart API for first-day close | Only free source with usable IPO-date closes |
| Jay Ritter's `IPO-age.xlsx` for the universe | Free, and carries offer date, VC flag and founding year |
| Name matching + redirect handling, with a human-review queue | Parent/subsidiary/product relationships are judgment calls, so they get escalated rather than guessed |
| Controls: `vc`, `tech`, `log(proceeds)`, year FE | Underwriter reputation needs prospectus name-matching; no free news database spans the window |

Because the control set is narrow, the headline number is best read as an
unconditional difference between the two groups rather than a conditional
effect. Notebook 03 makes that explicit.
"""),
    code("""
import json, sys, subprocess
from pathlib import Path
import pipeline as P

print("data directory:", P.ROOT)
for f in sorted((P.RAW).iterdir()):
    print(f"  {f.name:26s} {f.stat().st_size/1e6:8.2f} MB")
"""),
    md("""
## Source 1 — the IPO universe

Jay Ritter's `IPO-age.xlsx` (University of Florida) lists 16,031 US IPOs from
1975 to 2025 with offer date, ticker, CUSIP, an ADR flag, a **VC-backing
flag**, post-issue shares and founding year — a free substitute for the
commercial IPO databases that normally supply this.

Screens applied, following standard IPO-sample practice: drop ADRs, drop
blank-check/SPAC vehicles (ticker suffix `U`, or "Acquisition"/"Blank Check"
in the name), require a ticker.
"""),
    code("""
univ = P.load_universe(2014, 2024)
print(f"universe after screens: {len(univ)} IPOs")

import collections
by_year = collections.Counter(f["year"] for f in univ)
for y in sorted(by_year):
    print(f"  {y}  {by_year[y]:4d}  {'#' * (by_year[y] // 6)}")
"""),
    md("""
## Source 2 — CIK resolution

`company_tickers.json` covers only ~10,400 *current* filers, which misses every
firm that has since delisted or been renamed. `cik-lookup-data.txt` (40 MB)
carries all historical company names, and lifts the match rate from 47% to 95%.
"""),
    code("""
hits = 0
for f in univ:
    f["cik"], f["cik_via"] = P.resolve_cik(f)
    hits += bool(f["cik"])
print(f"CIK resolved: {hits}/{len(univ)} ({hits/len(univ):.1%})")
print("  by method:", collections.Counter(f["cik_via"] for f in univ))
"""),
    md("""
## Sources 3 & 4 — offer price and first-day close

**Offer price** comes from the 424B prospectus cover on EDGAR, located by
searching a firm's filing history for a `424B*` within 25 days of the offer
date, then regexing the cover page. Only the first 900 KB of each document is
fetched, which keeps the crawl to a few hundred MB instead of several GB.

**First-day close** comes from Yahoo's chart API. Two traps handled:

1. Yahoo's `close` series is **split-adjusted**, so a later reverse split would
   inflate a historical price. We un-adjust using the split events the API
   returns.
2. Yahoo returns placeholder records (`instrumentType: MUTUALFUND`,
   `firstTradeDate: null`) for delisted tickers rather than an error. Those are
   rejected, and we require the matched bar to be within 7 days of the offer
   date so a series that starts years late cannot masquerade as day one.

Both stages are cached per firm on disk, so the crawl is resumable.
Run `uv run --with openpyxl python build_panel.py` to (re)build.
"""),
    code("""
panel_path = P.OUT / "panel.json"
if not panel_path.exists():
    raise SystemExit("run build_panel.py first")
rows = json.loads(panel_path.read_text())
print(f"panel rows: {len(rows)}")
print(f"attrition: {len(univ)} universe -> {len(rows)} with price AND offer "
      f"({len(rows)/len(univ):.0%})")
"""),
    md("""
### Why the attrition is what it is

The dominant loss is **survivorship in free price data**. Firms that delisted —
acquired, taken private, or bankrupt — have been purged from Yahoo. Spot checks
on 2014 failures (GlycoMimetics, CHC Group, EP Energy, Cypress Energy Partners)
confirm these are genuine delistings, not lookup bugs.

This biases the sample toward survivors. It is stated as a limitation in
notebook 03 rather than corrected, because correcting it requires exactly the
paid data this project is avoiding.
"""),
    code("""
by_year_panel = collections.Counter(r["year"] for r in rows)
print("coverage by year (kept / universe):")
for y in sorted(by_year):
    k = by_year_panel.get(y, 0)
    print(f"  {y}  {k:4d}/{by_year[y]:4d}  {k/by_year[y]:5.0%}")
"""),
])

# ===========================================================================
# 02 - Wikipedia matching
# ===========================================================================
write("02_wikipedia_matching.ipynb", [
    md("""
# 02 — Coding the Wikipedia indicator

This is the hard part of the project, and the part that normally gets done by
hand. Assigning a Wikipedia article to an IPO firm involves several judgment
calls, plus a couple of exclusions.

### The coding rule

An IPO firm is coded `Wikipedia = 1` when an article about it existed **before
the first trading day** with **at least 30 words in the main body**. Redirect
pages and disambiguation-style mentions are coded 0.

### What we automate

Matching on the firm's own name, plus **redirect following**, which catches
renames for free (`Liberty Oilfield Services` → `Liberty Energy`). The harder
relationships — parent company, major subsidiary, spun-off entity,
predecessor, core product — are judgment calls and are *not* automated.

### Three-way outcome, not two

A naive matcher silently guesses on hard cases and biases the result. Ours
returns `accept`, `reject`, or **`ambiguous`** — the last routed to human review
with the evidence attached. The motivating pair:

- `Nu Holdings` → `Nubank` — zero token overlap, and **correct**
- `Array Technologies` → `ATI Technologies` — 0.5 overlap, and **wrong**
  (ATI was formerly "Array Technology Inc"; different company entirely)

Nothing in the API distinguishes these, so neither is auto-accepted.
"""),
    code("""
import json, collections
import pipeline as P

rows = json.loads((P.OUT / "panel.json").read_text())
print(f"panel rows: {len(rows)}")
print("wiki status:", collections.Counter(r["wiki_status"] for r in rows))
"""),
    md("""
## Validation against known answers

A small set of firms whose correct coding can be established independently
serves as a regression test. A case passes if it is either coded correctly
automatically, **or** correctly escalated to review (i.e. a reviewer looking at
the proposed title would reach the right answer).
"""),
    code("""
auto = esc = wrong = 0
print(f"{'firm':32s} {'exp':>3s}  outcome")
print("-" * 78)
for name, date, exp, note in P.BENCHMARK_CASES:
    r = P.wikipedia_flag(name, date)
    if r["status"] == "accept":
        good = r["wiki"] == exp
        auto += good; wrong += not good
        tag = f"AUTO wiki={r['wiki']}" + ("" if good else "  <-- WRONG")
    else:
        wb = r.get("would_be")
        good = wb == exp
        esc += good; wrong += not good
        tag = f"REVIEW would_be={wb}" + ("" if good else "  <-- MISLEADING")
    print(f"{name:32s} {exp:>3d}  {tag:34s} {str(r['title'])[:24]}")
print("-" * 78)
print(f"auto-correct {auto} | correctly escalated {esc} | wrong {wrong}  (of {len(P.BENCHMARK_CASES)})")
"""),
    md("""
## The human-review queue

Ambiguous cases are written to `data/out/review_queue.csv` with the proposed
article, its creation date, body word count and lead extract, so a reviewer can
adjudicate quickly. Notebook 03 reports the result three ways — excluding these
rows, coding them all 1, and coding them all 0 — so the conclusion can be
checked against the worst case.
"""),
    code("""
import csv
amb = [r for r in rows if r["wiki_status"] == "ambiguous"]
print(f"ambiguous cases needing review: {len(amb)}")

out = P.OUT / "review_queue.csv"
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["ticker", "company", "ipo_date", "proposed_article",
                "article_created", "body_words_at_ipo", "overlap",
                "auto_would_be", "lead_extract", "VERDICT_1_or_0"])
    for r in sorted(amb, key=lambda x: -(x["wiki_overlap"] or 0)):
        w.writerow([r["ticker"], r["name"], r["ipo_date"], r["wiki_title"],
                    (r["wiki_created"] or "")[:10], r["wiki_words"],
                    r["wiki_overlap"], r["wiki_would_be"],
                    (r["wiki_extract"] or "")[:200], ""])
print("wrote", out)

for r in sorted(amb, key=lambda x: -(x["wiki_overlap"] or 0))[:15]:
    print(f"  {r['ticker']:6s} {r['name'][:26]:26s} -> {str(r['wiki_title'])[:26]:26s} "
          f"ov={r['wiki_overlap']} would_be={r['wiki_would_be']}")
"""),
    md("""
## Auto-adjudicating the grey zone

Two free signals resolve most ambiguous cases, both suggested by the
motivating pair:

1. **Industry alignment** — EDGAR gives each filer's SIC description, compared
   against the article's lead extract by substring containment (so
   "Oil & Gas Field Services" matches "oilfield services").
2. **Already defunct** — an entity acquired or dissolved well before the IPO
   date cannot be the company going public. This is what separates
   `ATI Technologies` (bought by AMD in 2006) from the 2020 solar-tracker IPO.

Whatever these cannot settle stays in the queue for a human.
"""),
    code("""
adj = []
for r in amb:
    v, why, sig = P.refine_ambiguous(r)
    adj.append((r, v, why, sig))

auto = [x for x in adj if x[1] is not None]
human = [x for x in adj if x[1] is None]
print(f"ambiguous          : {len(adj)}")
print(f"  auto-adjudicated : {len(auto)}")
print(f"  still for human  : {len(human)}")

print("\\nvalidation on the confirmed cases:")
for c in P.ADJUDICATED_CASES:
    hit = [x for x in adj if x[0]["name"].lower().startswith(c["name"].lower()[:12])]
    if hit:
        r, v, why, sig = hit[0]
        print(f"  {c['name']:22s} truth={c['truth']} verdict={v} [{'OK' if v==c['truth'] else 'CHECK'}] {why}")
    else:
        print(f"  {c['name']:22s} (not in this sample)")
"""),
    code("""
# Apply the auto-adjudications back onto the panel rows.
by_key = {(r["ticker"], r["ipo_date"]): r for r in rows}
for r, v, why, sig in auto:
    tgt = by_key[(r["ticker"], r["ipo_date"])]
    tgt["wiki_refined"] = v
    tgt["wiki_refined_why"] = why
for r, v, why, sig in human:
    by_key[(r["ticker"], r["ipo_date"])]["wiki_refined"] = None
for r in rows:
    if r["wiki_status"] == "accept":
        r["wiki_refined"] = r["wiki"]
    elif r["wiki_status"] == "reject":
        # No plausible company article found -> a real zero.
        r["wiki_refined"] = 0

(P.OUT / "panel_refined.json").write_text(json.dumps(rows, indent=1))
n_final = sum(1 for r in rows if r.get("wiki_refined") is not None)
print(f"rows with a usable indicator: {n_final}/{len(rows)}")
print("  of which Wikipedia=1:",
      sum(1 for r in rows if r.get("wiki_refined") == 1))

# Anything left genuinely needs eyes.
with open(P.OUT / "review_queue.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["ticker", "company", "ipo_date", "sic_desc", "proposed_article",
                "article_created", "body_words_at_ipo", "overlap",
                "lead_extract", "VERDICT_1_or_0"])
    for r, v, why, sig in human:
        w.writerow([r["ticker"], r["name"], r["ipo_date"], r.get("sic_desc"),
                    r["wiki_title"], (r["wiki_created"] or "")[:10],
                    r["wiki_words"], r["wiki_overlap"],
                    (r["wiki_extract"] or "")[:200], ""])
print(f"\\nwrote review_queue.csv with {len(human)} rows needing a human")

# Human verdicts, once returned, take precedence over everything automated.
vpath = P.OUT / "review_verdicts.csv"
if vpath.exists():
    verdicts = {r["ticker"]: int(r["VERDICT"])
                for r in csv.DictReader(open(vpath)) if r.get("VERDICT") not in (None, "")}
    applied = 0
    for r in rows:
        if r["ticker"] in verdicts:
            r["wiki_refined"] = verdicts[r["ticker"]]
            r["wiki_verdict_source"] = "human"
            applied += 1
    (P.OUT / "panel_refined.json").write_text(json.dumps(rows, indent=1))
    print(f"applied {applied} human verdicts from review_verdicts.csv")
    print("  coded 1:", [t for t, v in verdicts.items() if v == 1])
"""),
    md("""
## Distribution of the indicator
"""),
    code("""
acc = [r for r in rows if r["wiki_status"] == "accept"]
n1 = sum(1 for r in acc if r["wiki"] == 1)
print(f"accepted rows      : {len(acc)}")
print(f"  Wikipedia = 1    : {n1} ({n1/max(len(acc),1):.1%})")
print(f"  Wikipedia = 0    : {len(acc)-n1}")
print()
print("\\nreasons for a zero:")
for reason, c in collections.Counter(
        r["wiki_reason"] for r in acc if r["wiki"] == 0).most_common():
    print(f"  {reason:24s} {c}")
"""),
])

# ===========================================================================
# 03 - results
# ===========================================================================
write("03_results.ipynb", [
    md("""
# 03 — Does the relationship hold?

Testing the statement:

> **Companies that already had a Wikipedia article before going public are more
> underpriced.**

`underpricing = (first_close − offer_price) / offer_price × 100`
"""),
    code("""
import json, math, collections
import numpy as np, pandas as pd
import pipeline as P

src = P.OUT / "panel_refined.json"
if not src.exists():
    src = P.OUT / "panel.json"
rows = json.loads(src.read_text())
df = pd.DataFrame(rows)
if "wiki_refined" not in df.columns:
    df["wiki_refined"] = df["wiki"].where(df.wiki_status == "accept")
print(f"raw panel: {len(df)}  (source: {src.name})")
df.head(3)[["ticker", "ipo_date", "offer_price", "close", "underpricing", "wiki", "wiki_status"]]
"""),
    md("""
## Cleaning

The raw panel has a standard deviation of **249 pp**, which is impossible for
first-day IPO returns (the real figure is ~40 pp). Two screens fix it, and the
second one is the important one.

1. **Offer price >= $5** — a conventional screen. Also removes the
   nano-caps where prospectus parsing is least reliable, and direct listings
   whose "reference price" is not an offer price at all.
2. **No post-IPO split** — Yahoo's `close` series is split-adjusted, and its
   split history for micro-caps is *incomplete*, so un-adjusting is impossible.
   Verified directly: E-Home Household reports seven reverse splits and still
   lands 10× too high; reAlpha reports one 1:25 split when it clearly had more.
   Split-adjusted rows carry sd = 421 pp against 133 pp for the rest.

Then winsorize at 1/99 so genuine large pops are retained without dominating.

**This second screen is a look-ahead restriction** — reverse splits happen to
firms that later collapse, so the retained sample skews toward survivors on top
of the survivorship already present in free price data. It is recorded as a
limitation rather than corrected, because correcting it needs CRSP.
"""),
    code("""
n0 = len(df)
df = df[df.underpricing.notna()]

# Screen 1: offer price >= $5. A conventional screen, and it drops
# the nano-cap listings where prospectus parsing is least reliable.
df = df[(df.offer_price >= 5) & (df.offer_price <= 500)].copy()
print(f"offer price in [$5, $500]      : {len(df)}/{n0}")

# Screen 2: no post-IPO split. Yahoo's `close` is split-adjusted, and its
# split history for micro-caps is demonstrably INCOMPLETE, so the series
# cannot be reliably un-adjusted. E-Home Household (EJH) reports seven
# reverse splits and still lands 10x too high; reAlpha (AIRE) reports one
# 1:25 split when it plainly had more. Keeping these rows put the sample sd
# at 421 pp against 133 pp for unadjusted rows.
#
# This is a LOOK-AHEAD restriction: reverse splits happen to poor performers,
# so the retained sample skews toward firms that did not later collapse.
# Recorded as a limitation, not corrected.
pre = len(df)
df["split_factor"] = df.split_factor.fillna(1.0)
df = df[df.split_factor == 1.0].copy()
print(f"no post-IPO split adjustment   : {len(df)}/{pre}")

lo, hi = df.underpricing.quantile([0.01, 0.99])
df["up_w"] = df.underpricing.clip(lo, hi)
print(f"winsorized at [{lo:.1f}, {hi:.1f}] pp")
print(f"\\nunderpricing: mean {df.up_w.mean():+.2f}  median {df.up_w.median():+.2f}  "
      f"sd {df.up_w.std():.2f}")
print("(US IPO benchmark: mean ~18%, median ~10%, sd ~40% -- this is in range)")
"""),
    md("""
## The main test — difference in means

This is the statement, tested directly. Welch's t-test (unequal variances),
restricted to rows the matcher accepted.
"""),
    code("""
from scipy import stats

# Primary specification: matcher-accepted codings, plus rejects as genuine
# zeros (no plausible company article found). Ambiguous rows are held out and
# bounded in the robustness section below.
# Primary: matcher-accepted codings, rejects as genuine zeros, plus the
# human-adjudicated rows returned in review_verdicts.csv.
human_done = df.get("wiki_verdict_source", pd.Series(index=df.index, dtype=object)) == "human"
d = df[df.wiki_status.isin(["accept", "reject"]) | human_done].copy()
d["wiki"] = d.wiki_refined.where(human_done, d.wiki).fillna(0).astype(int)
print(f"primary sample: {len(d)}  (accept {(d.wiki_status=='accept').sum()}, "
      f"reject {(d.wiki_status=='reject').sum()}, human {int(human_done.sum())})")
a = d[d.wiki == 1].up_w
b = d[d.wiki == 0].up_w

t, p = stats.ttest_ind(a, b, equal_var=False)
se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
diff = a.mean() - b.mean()

print(f"Wikipedia = 1 : n={len(a):4d}  mean underpricing {a.mean():+7.2f}%")
print(f"Wikipedia = 0 : n={len(b):4d}  mean underpricing {b.mean():+7.2f}%")
print(f"\\ndifference     : {diff:+.2f} pp")
print(f"std error      : {se:.2f}")
print(f"t-statistic    : {t:+.2f}")
print(f"p-value        : {p:.4f}")
print(f"95% CI         : [{diff-1.96*se:+.2f}, {diff+1.96*se:+.2f}] pp")
print()
"""),
    md("""
## With controls

We can afford `vc` (Ritter), `tech` (SIC 3570–3579 / 7370–7379 / 3661–3674,
from EDGAR), `log(proceeds)` (offer price × shares offered, from the
prospectus cover) and year fixed effects.

We cannot afford underwriter reputation, share overhang, or a pre-IPO news
count — all three normally matter for underpricing and plausibly correlate
with Wikipedia presence, so this estimate retains confounding a fuller
specification would remove.
"""),
    code("""
import statsmodels.formula.api as smf

TECH_SIC = lambda s: bool(s) and (
    3570 <= int(s) <= 3579 or 7370 <= int(s) <= 7379 or 3661 <= int(s) <= 3674)

d = d.copy()
d["tech"] = d.sic.apply(lambda s: int(TECH_SIC(s)) if str(s).isdigit() else 0)
d["vc"] = d.vc.fillna(0).astype(int)
d["proceeds"] = d.offer_price * d.shares_offered
d["log_proceeds"] = np.log(d.proceeds.where(d.proceeds > 0))
d["year"] = d.year.astype(str)

est = d.dropna(subset=["log_proceeds"])
m = smf.ols("up_w ~ wiki + vc + tech + log_proceeds + C(year)", data=est).fit(cov_type="HC1")
print(m.summary().tables[1])
print(f"\\nn = {int(m.nobs)}   adj R2 = {m.rsquared_adj:.3f}")
ci = m.conf_int().loc["wiki"]
print(f"\\nWikipedia coefficient: {m.params['wiki']:+.2f} pp  "
      f"(SE {m.bse['wiki']:.2f}, p={m.pvalues['wiki']:.4f})")
print(f"95% CI: [{ci[0]:+.2f}, {ci[1]:+.2f}]")
"""),
    md("""
## Robustness to the ambiguous cases

The matcher escalated some firms rather than guessing. Bounding the result by
coding them all 1 and all 0 shows whether the conclusion depends on them.
"""),
    code("""
def diff_means(frame):
    a, b = frame[frame.wiki == 1].up_w, frame[frame.wiki == 0].up_w
    if len(a) < 2 or len(b) < 2:
        return None
    se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
    dd = a.mean() - b.mean()
    return dd, se, dd/se, len(a), len(b)

variants = {}
# Primary: matcher-accepted codings plus rejects as genuine zeros. Ambiguous
# rows are excluded here and bounded below.
hd = df.get("wiki_verdict_source", pd.Series(index=df.index, dtype=object)) == "human"
base = df[df.wiki_status.isin(["accept", "reject"]) | hd].copy()
base["wiki"] = base.wiki_refined.where(hd, base.wiki).fillna(0).astype(int)
variants["PRIMARY (accept+reject)"] = base
variants["+ auto-adjudicated"] = (
    lambda t: t.assign(wiki=t.wiki_refined.astype(int))
)(df[df.wiki_refined.notna()])
amb1 = df[df.wiki_status != "api_error"].copy()
amb1["wiki"] = amb1.wiki.fillna(0)
amb1.loc[amb1.wiki_status == "ambiguous", "wiki"] = 1
variants["+ ambiguous all -> 1"] = amb1
amb0 = df[df.wiki_status != "api_error"].copy()
amb0["wiki"] = amb0.wiki.fillna(0)
amb0.loc[amb0.wiki_status == "ambiguous", "wiki"] = 0
variants["+ ambiguous all -> 0"] = amb0

print(f"{'specification':26s} {'n1':>5s} {'n0':>5s} {'diff':>8s} {'SE':>7s} {'t':>7s}")
print("-" * 64)
for k, v in variants.items():
    r = diff_means(v)
    if r:
        dd, se, tt, n1, n0 = r
        print(f"{k:26s} {n1:5d} {n0:5d} {dd:+8.2f} {se:7.2f} {tt:+7.2f}")
"""),
    md("""
## Also: unwinsorized, and by era
"""),
    code("""
raw = base
a, b = raw[raw.wiki == 1].underpricing, raw[raw.wiki == 0].underpricing
se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
print(f"unwinsorized: diff {a.mean()-b.mean():+.2f} pp, SE {se:.2f}, "
      f"t {(a.mean()-b.mean())/se:+.2f}")

print("\\nby period:")
for label, lo_y, hi_y in [("2014-2018", 2014, 2018), ("2019-2024", 2019, 2024)]:
    sub = raw[(raw.year.astype(int) >= lo_y) & (raw.year.astype(int) <= hi_y)]
    r = diff_means(sub)
    if r:
        dd, se, tt, n1, n0 = r
        print(f"  {label}  n={n1+n0:4d}  diff {dd:+7.2f} pp  t {tt:+5.2f}")
"""),
    md("""
## Verdict

See the printed output above. Interpretation guidance:

- The **sign** of the difference is the primary result. The statement predicts
  positive.
- With this sample size the test is underpowered against an effect of a few
  percentage points, so a confidence interval that includes zero is the
  expected outcome a substantial fraction of the time **even if the effect is
  real**.
- This is a **descriptive difference between two groups**, not a causal
  estimate. Nothing here identifies what Wikipedia does; the design has no
  instrument and no natural experiment.
- Known biases here: survivorship in free price data (delisted
  firms are absent), an incomplete control set (no `top_tier`, `overhang`,
  `log_news`), and rule-(1)-only matching, which misclassifies some Wikipedia
  firms as non-Wikipedia and **attenuates the estimate toward zero**.
"""),
])

print("\\ndone")
