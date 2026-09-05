# Does Google Trends fit this system?

**Decision: out of scope.** Not a second interface, not a forced fit — excluded,
for a reason that is structural rather than aesthetic.

## What it actually returns

A normalized index, 0–100, per time bucket. Three properties matter:

1. **It is not discrete items.** No ids, no authors, no per-event timestamps.
   There is nothing to deduplicate, nothing to attribute, nothing to delete.
2. **It is relative, not absolute.** The 100 is the maximum *within that query* —
   its window and its set of comparison terms. Change either and every value
   changes. It is not a count of anything.
3. **It is sampled.** The same query run twice returns slightly different
   numbers.

## Why it cannot use `fetch(since) -> list[RawMention]`

`RawMention` models one observed event with provenance: a `source_uid` to
deduplicate on, a `posted_at`, an `author_hash` so distinct people can be
counted, a URL a human can open. A Trends index has none of those.

Forcing it in would mean inventing rows — emitting an index value of 63 as 63
synthetic mentions with fabricated uids and null authors. That corrupts
`mention_count` and `unique_authors`, both of which are supposed to mean
"discrete things that actually happened", and it breaks retention, since there
is no raw content to delete after 90 days. It is exactly the fabricated data the
seed rules forbid, arriving through a different door.

## Why a second interface still would not save it

The obvious fix is a second protocol — `SignalAdapter.fetch_series(term, since)
-> list[SeriesPoint]` landing in a `signal_daily` table, read by the hype score
alongside `mention_daily`. That shape is fine. **The data is not**, and here is
the specific blocker:

> **The hype score is cohort-relative.** It z-scores an issuer against every
> other active issuer. Trends values are only comparable *within a single
> query*, and a query accepts at most five terms. With 60+ issuers, no set of
> batched queries produces mutually comparable numbers — batch A's 100 and batch
> B's 100 are different absolute volumes, and there is no conversion between
> them.

So the one thing this project needs Trends for is the one thing Trends cannot
do. That is a property of the data, not of our plumbing, and no interface fixes
it.

## And the access route is closed anyway

There is no official Google Trends API. Every Python library for it (`pytrends`
and its successors) scrapes an internal endpoint. That runs straight into the
hard constraint in `PROJECT_BRIEF.md` §7: no routing around a platform's terms
via a scraper, and no search-derived estimates standing in for measurements.
Adding it would contradict the Reddit API access request this project has
submitted.

Even if the cohort problem were solved, this alone would rule it out.

## What would be acceptable instead

If a term-level interest signal is wanted later, the requirements fall out of
the above:

- **absolute values**, not window-normalized — so two issuers are comparable
  without having been queried together;
- **a real API with terms that permit this use**;
- **reproducible** — the same query returns the same number.

If such a source is adopted, the `SignalAdapter` + `signal_daily` shape sketched
above is the right one, and adding that table would need explicit sign-off since
the schema in `PROJECT_BRIEF.md` §4 is fixed.

Until then the hype score is built from things that were actually posted, each of
which has a URL a human can open.
