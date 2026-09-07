# Source status

| source | auth | status |
|---|---|---|
| SEC EDGAR | none; identified `User-Agent` | **in use** |
| Hacker News (Algolia) | none | **in use** |
| Market data (prices) | licensed, API key | pending key — see below |
| News | licensed, API key | pending key |
| Reddit | OAuth, approval pending | not implemented |
| GDELT | none | **cut** — see below |

## GDELT: cut

The adapter was written to the documented DOC 2.0 `artlist` schema and its
transform was unit-tested, but **it never received a single live response**
across three attempts on three separate days:

- Every request returned `429`, including single requests after 90 and 150
  seconds of complete silence — so this was never our request rate.
- GDELT's `summary` endpoint returned `200` in the same session, so the host was
  reachable and we were not IP-banned.
- The refusal body arrives as plain text, sometimes with a `200`, which
  `.json()` turns into a parse error that reads like a bug in our code rather
  than a rate limit. That behaviour is worth remembering for any future
  integration.

Removed rather than left in place. An adapter that has never worked is not a
source; keeping it in the list would have overstated what this pipeline
actually ingests.

## Why market data is licensed

`PROJECT_BRIEF.md` §7 prohibits routing around a platform's terms. That has now
decided three integrations:

1. **SerpAPI and search-scrapers** — rejected. Search-result counts are
   estimates, not measurements, and a hype score built on them is
   irreproducible.
2. **Unauthenticated Reddit** (`reddit.com/*.json`) — rejected. It contradicts
   the API access request this project has submitted.
3. **Stooq** — rejected. It serves a JavaScript proof-of-work challenge;
   retrieving a CSV requires computing a SHA-256 nonce to satisfy browser
   verification. That is defeating an access control.

A fourth was considered and also rejected: **Yahoo Finance's chart endpoint**.
It is technically trivial — no auth, no challenge, no User-Agent spoofing, and a
plain request returns clean JSON for every ticker tested. But it is undocumented
and grants no permission for this use, unlike SEC EDGAR which explicitly does.
"Not circumvention, but not licensed either" is the wrong side of the line, and
the line had already been drawn twice.

So prices and news come from a provider that grants the use in writing. Every
such provider requires an API key, because a licence is granted to an identified
party — that is the cost of consistency here, and it is the right cost to pay.
