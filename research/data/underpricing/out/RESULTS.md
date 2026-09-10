# Results

**Statement tested:** *Companies that already had a Wikipedia article before
going public are more underpriced.*

**Verdict: supported as a descriptive difference, at the 10% level.**
The gap is **+5.55 pp (p = 0.091)**, stable across specifications, but the
confidence interval includes zero and the effect does not survive controls.

---

## Headline

| | n | mean underpricing |
|---|---:|---:|
| Wikipedia = 1 | 128 | **+22.63%** |
| Wikipedia = 0 | 401 | **+17.08%** |
| **Difference** | | **+5.55 pp** |

- Standard error **3.26**, t = **+1.70**, **p = 0.091**
- 95% CI **[-0.85, +11.94] pp**

Includes 28 human-adjudicated matches (SSTI, ELAN, ULS coded 1; the rest 0).

## Sanity check on the dependent variable

mean **+18.71%**, median **+10.08%**, sd **30.41** - squarely in line with the
US IPO literature (~18% / ~10% / ~40%). An earlier version of this panel had
sd = 249 pp; see *Cleaning* below.

## Robustness

The estimate barely moves across every treatment of the ambiguous matches:

| Specification | n1 | n0 | diff | SE | t |
|---|---:|---:|---:|---:|---:|
| **Primary** (accept + reject + human) | 128 | 401 | **+5.55** | 3.26 | +1.70 |
| + auto-adjudicated | 180 | 418 | +5.37 | 2.77 | +1.94 |
| + ambiguous all -> 1 | 214 | 388 | +5.00 | 2.59 | +1.93 |
| + ambiguous all -> 0 | 125 | 477 | +4.57 | 3.25 | +1.40 |

Range **+4.57 to +5.55 pp**. Three of four clear the 10% level. The conclusion
does not hinge on how the hard matches are resolved.

Unwinsorized: **+10.05 pp** (SE 6.84, t = +1.47) - same sign, larger, noisier.

By era: 2014-2018 **+5.02 pp**; 2019-2024 **+6.06 pp**. Consistent.

### The two redirect cases

Two human `1` verdicts were redirects at their IPO date, which the 30-word
content rule would code as zero. ELAN is defensible on the grounds that Elanco
was spun out of Eli Lilly and the page redirected to the parent. All three
readings agree:

| Reading | diff | t | p |
|---|---:|---:|---:|
| As adjudicated (SSTI=1, ELAN=1) | +5.55 | +1.70 | 0.091 |
| ELAN=1 as a spinoff, SSTI -> 0 | +5.45 | +1.66 | 0.098 |
| Strict redirect rule (both -> 0) | +5.15 | +1.56 | 0.120 |

## With controls, the effect disappears

| variable | coef | SE | p |
|---|---:|---:|---:|
| **wiki** | **+0.65** | 3.65 | 0.859 |
| vc | +13.97 | 2.82 | <0.001 |
| log_proceeds | +3.95 | 0.96 | <0.001 |
| tech | +5.29 | 4.33 | 0.221 |

n = 459, adj R2 = 0.150, year FE, HC1 errors.

**This is the most important caveat in the whole project.** VC backing and
offer size are each strongly associated with underpricing *and* with having a
Wikipedia article, and together they absorb the entire raw gap.

Two readings, and the data here cannot separate them:

1. The unconditional difference is confounded - Wikipedia presence is largely
   a proxy for being a large, VC-backed firm.
2. Controlling for proceeds may over-absorb the effect of interest, since firm
   size and public visibility are near-collinear.

Either way, nothing here supports a claim that Wikipedia *causes* underpricing.

## Cleaning that mattered

Raw panel sd was **249 pp**, impossible for first-day returns. Two screens:

1. **Offer price >= $5** - conventional screen; also removes direct listings,
   whose "reference price" is not an offer price. Amplitude parsed at $3.33
   and Warby Parker at $6.11 against reference prices of $35 and $40.
2. **No post-IPO split** - Yahoo's `close` is split-adjusted and its split
   history for micro-caps is **incomplete**, so un-adjusting is impossible.
   Verified: E-Home Household reports seven reverse splits and still lands 10x
   too high; reAlpha reports one 1:25 split when it plainly had more.
   Split-adjusted rows: sd 421 pp. Everything else: sd 133 pp.

899 -> 766 (price screen) -> 602 (split screen) -> 529 in the primary sample.

## Matcher validation

- **Benchmark cases: 6/6, zero wrong** - 4 auto-correct, 2 correctly escalated
  to review.
- **Household-name recall: 16/18 (89%)** coded 1 automatically (Uber, Lyft,
  Airbnb, DoorDash, Snowflake, Reddit, Etsy, GoPro, ...). The two misses (Zoom,
  Rivian) were escalated, not silently zeroed.
- Wikipedia = 1 rate: **24.4%**. Plausible for a window dominated by small
  biotechs and Chinese small-caps that mostly lack articles, though some
  attenuation from name-only matching is likely, and it biases toward zero.

## Why this is not significant at 5%, and what would fix it

At the observed effect (5.55 pp) and dispersion (sd 30.4), reaching t = 1.96
needs roughly **n = 700** in the primary sample against our **529**. The
shortfall is entirely attrition: 53% of the universe has no free price data,
and the split screen costs another 21%.

The cheapest fix is not a bigger window - it is **one month of a
survivorship-bias-free price feed** (EODHD ~$20, Sharadar ~$50), which would
restore both the delisted firms and the split-adjusted ones, roughly doubling n.

## How to state the finding

> In a sample of 529 US IPOs from 2014-2024, firms with a pre-existing
> Wikipedia article were underpriced about 5.5 percentage points more on
> average than firms without one (95% CI -0.9 to +11.9, p = 0.09). The
> estimate is stable across matching specifications but does not survive
> controls for VC backing and offer size.

Dropping the final clause makes it dishonest; dropping the CI makes it
overconfident.

## Limitations

1. **Survivorship** - delisted firms are absent from free price data.
2. **Look-ahead** - the split screen drops firms that later reverse-split,
   which are disproportionately poor performers.
3. **Incomplete controls** - no underwriter reputation, share overhang, or
   pre-IPO news count.
4. **Matcher attenuation** - name-only matching misses some real articles,
   pushing the estimate toward zero.
5. **Not causal** - this is a descriptive difference between two groups.
