# IPO Surveillance Platform — Build Brief

You are building this project with me from scratch. Read this entire brief before writing any code.

---

## 1. Context

I'm a CS junior. I know Django, React components, and SQL. I am **new to FastAPI, the Next.js App Router, and async Python**. I am building this to learn and to have something substantial to discuss in engineering interviews at a quantitative trading firm.

Because of that, two rules govern everything:

- **Explain before you generate.** When you introduce a concept I haven't used (dependency injection, server vs. client components, connection pooling, cursor pagination), give me 3–5 sentences on the mental model before the code.
- **I need to be able to defend every line.** Do not add abstractions "for later." If you think something is needed, argue for it first.

Check `.claude/skills/` for my existing skills and use them where they apply.

---

## 2. What the system does

Ingests upcoming US IPO registrations from SEC EDGAR, ingests social chatter about those companies from Reddit, links the unstructured chatter to pre-ticker issuers, and scores each issuer on two axes:

- **Hype** — social mention volume and velocity, normalized.
- **Quality** — fundamentals and underwriter strength from the filings.

The dashboard surfaces two views: **Hidden Gems** (low hype, high quality) and **Overheated** (hype spiking without fundamental support).

Framing note: this is a *surveillance and anomaly detection* tool, not a stock recommender. Language in code comments, README, and UI should reflect that.

---

## 3. Locked technical decisions

Do not re-litigate these. If you think one is wrong, say so once, briefly, then follow it.

**Backend**
- Python 3.12+, FastAPI
- `asyncpg` with hand-written SQL. **No ORM.** No SQLAlchemy, no SQLModel, no Tortoise.
- Migrations: numbered `.sql` files in `migrations/`, applied by a small runner script. No Alembic.
- `pydantic-settings` for config, `httpx` for HTTP (all sources; no per-platform SDKs)
- `pytest` + `httpx.AsyncClient` for tests
- Workers run in a **separate process** from the API, scheduled with APScheduler

**Frontend**
- Next.js 16, App Router, TypeScript
- Tailwind + shadcn/ui for components
- Tremor for charts (built for finance dashboards)
- TanStack Query for client-side fetching
- **No Server Actions.** FastAPI is the only backend. Next.js route handlers exist only to proxy auth cookies.

**Infra**
- Postgres 17 in Docker locally; Neon when deployed
- `docker-compose.yml` for db + api + worker

---

## 4. Database schema

Build exactly this in migration `001`. Ask me before adding a table.

```
issuers            id, cik, legal_name, normalized_name, ticker (null until listed),
                   exchange, sector, status (filed|priced|listed|withdrawn),
                   first_filed_at, created_at, updated_at

aliases            id, issuer_id FK, alias, normalized_alias,
                   kind (legal|brand|cashtag|informal), created_at
                   -- powers entity resolution; one issuer has many

filings            id, issuer_id FK, cik, accession_no UNIQUE, form_type,
                   filed_at, primary_doc_url, fetched_at

offerings          id, issuer_id FK, price_low, price_high, price_final,
                   shares_offered, float_shares, expected_list_date,
                   lockup_expires_at, source_filing_id FK

underwriters       id, name, normalized_name
offering_underwriters   offering_id FK, underwriter_id FK, role (lead|co_manager)

mentions           id, source (hn|gdelt|reddit), source_uid, issuer_id FK NULL,
                   matched_alias_id FK NULL, match_confidence NUMERIC(3,2),
                   needs_review BOOL, author_hash, channel, title, body_excerpt,
                   url, engagement_score, posted_at, ingested_at
                   UNIQUE (source, source_uid)
                   -- author_hash is a salted SHA-256. Raw usernames are never
                   -- stored. Rows are deleted 90 days after posted_at.

mention_daily      issuer_id FK, day DATE, source, mention_count,
                   unique_authors, weighted_engagement
                   UNIQUE (issuer_id, source, day)

fundamentals       id, issuer_id FK, period_end, revenue, revenue_prior,
                   gross_margin, net_income, total_debt, cash,
                   source_filing_id FK, extracted_at

scores             id, issuer_id FK, as_of, hype_score, quality_score,
                   gem_score, components JSONB
                   UNIQUE (issuer_id, as_of)
```

**Indexes required:** `mentions(issuer_id, posted_at DESC)`, `mentions(needs_review) WHERE needs_review`, `mention_daily(day DESC)`, `issuers(normalized_name)`, `aliases(normalized_alias)`.

**Design points to preserve:**
- `mentions.issuer_id` is nullable with a `match_confidence` and `needs_review` flag. Unmatched and low-confidence matches are **kept**, not discarded. Match precision must stay auditable.
- `scores` is precomputed by the worker, never calculated per-request.
- `mentions.author_hash` is a salted SHA-256 of the platform username; the raw username is never written to the database. Its only supported use is `mention_daily.unique_authors`. Salt comes from `MENTION_HASH_SALT`.
- **Retention:** individual `mentions` rows are deleted 90 days after `posted_at`. `mention_daily` aggregates are permanent. The raw text is an input to a metric, not an archive.

---

## 5. API design

Versioned under `/api/v1`. All list endpoints use cursor pagination and return `{ "data": [...], "meta": { "next_cursor": ... } }`.

```
GET  /health
GET  /api/v1/issuers            ?status= &sort=hype|quality|gem|filed_at &limit= &cursor=
GET  /api/v1/issuers/{id}
GET  /api/v1/issuers/{id}/mentions      ?days=30   -> daily time series
GET  /api/v1/issuers/{id}/filings
GET  /api/v1/gems               ?limit=     -> low hype, high quality
GET  /api/v1/signals/spikes     ?days=7     -> mention velocity anomalies
GET  /api/v1/review/queue                   -> low-confidence matches for manual review
POST /api/v1/review/{mention_id}            -> confirm or reject a match
```

Every response shaped by an explicit Pydantic `response_model`. DB pool opened in `lifespan`, handed to routes via `Depends`.

---

## 6. Build order

**Work one phase at a time. Stop at the end of each phase and wait for me.** Do not run ahead.

### Phase 0 — Skeleton
Repo layout, `docker-compose.yml`, Postgres up, migration runner script, migration `001` with the full schema above, `/health` endpoint returning a real DB round-trip. `README.md` with setup steps.

*Done when:* `docker compose up` works and `curl localhost:8000/health` returns healthy with a DB timestamp.

### Phase 1 — Vertical slice
Seed 10 real recent IPO issuers by hand into `issuers`. Build `GET /api/v1/issuers` with pagination. Scaffold Next.js, render the list in a server component, deploy nothing yet.

Walk me through: `Depends`, `response_model`, CORS setup, and the server/client component boundary.

*Done when:* the Next.js page shows 10 rows from Postgres through FastAPI.

### Phase 2 — EDGAR ingestion
Worker process polling EDGAR for new `S-1`, `S-1/A`, `F-1`, `424B4` filings. Upsert into `issuers` and `filings`. Extract underwriters from the prospectus cover page. Parse price range from `S-1/A`.

Respect SEC rules: max 10 req/sec, descriptive `User-Agent` with contact email, exponential backoff. Put the rate limiter in one place.

*Done when:* the worker runs on a schedule and populates real filings without me touching the DB.

### Phase 3 — Social ingestion + entity resolution

**Source adapters.** Define one interface and implement it per platform:

```
class SourceAdapter(Protocol):
    name: str
    async def fetch(self, since: datetime) -> list[RawMention]: ...
```

`RawMention` is a platform-neutral record (source, source_uid, author_hash,
channel, title, body_excerpt, url, engagement_score, posted_at). Everything
downstream — matching, scoring, the review queue — sees only `RawMention` and
never a platform-specific shape.

**First implementations, both keyless:**
- **Hacker News** via the Algolia search API
- **GDELT** for news

**Reddit is not in the initial build.** Reddit now requires explicit approval
before API access. The access request is submitted separately; if and when it is
granted, Reddit becomes one more adapter behind the same interface, using OAuth
with real credentials. Nothing else changes. See the hard constraint in §7.

**Entity resolution.** Alias generation per issuer (legal name, stripped
suffixes, brand name, cashtag). Matching with a confidence score.

**Do not use naive substring matching.** Company names like "Circle" and
"Figure" are ordinary English words. Build: normalize → candidate generation →
scored match → threshold. Below threshold, store with `needs_review = true`.

**Retention sweep.** Scheduled job deleting `mentions` rows older than 90 days
by `posted_at`. `mention_daily` aggregates are permanent. The schema already
carries this as a `COMMENT ON TABLE` and a supporting index.

Write down the precision/recall tradeoff in `docs/matching.md`. I need to explain
this in interviews.

*Done when:* mentions from at least two keyless sources land in the DB with
confidence scores, the review queue endpoint returns the ambiguous ones, and the
retention sweep runs on a schedule.

### Phase 4 — Scoring
Nightly rollup into `mention_daily`. Hype score from volume + velocity, z-scored across the active cohort. Quality score from revenue growth, margin, debt, underwriter tier. Store components in `scores.components` JSONB so the dashboard can explain any number it shows.

*Done when:* `/api/v1/gems` returns a defensible ranking.

### Phase 5 — Dashboard
Gems table, issuer detail page with a mention time-series chart (Tremor), spike alerts view, review queue UI. TanStack Query for anything polling.

### Phase 6 — Hardening
pytest suite with a test DB, structured logging, error handling, Dockerfiles, deploy to Vercel + Railway/Render + Neon. README with architecture diagram and screenshots.

---

## 7. Rules of engagement

- Stop and wait after every phase.
- Ask before adding any dependency not listed in §3.
- No f-string SQL. `$1`-style parameters only.
- No secrets in code — `.env` + `pydantic-settings`, and `.env.example` committed.
- Every non-obvious decision gets a one-line comment explaining *why*, not *what*.
- If a phase turns out bigger than expected, tell me and propose a split rather than producing 2000 lines silently.
- When I ask "why did you do it that way," give me the real tradeoff, including the case against.

**Hard constraints — these are not tradeoffs.**

- **Never use unauthenticated Reddit endpoints.** No `reddit.com/*.json`, no
  scraping, no undocumented JSON routes, with or without a User-Agent dodge. It
  violates Reddit's Developer Terms, it contradicts the API access request I
  have submitted, and it 403s from datacenter IPs the moment this deploys. If
  Reddit is added it goes through OAuth with approved credentials or not at all.
- **Never store raw usernames.** Social authors are persisted only as
  `mentions.author_hash`, a salted SHA-256. The only supported use is counting
  distinct authors. Do not attempt to profile, re-identify, or derive
  characteristics of any user.
- **Read-only, non-commercial.** This system ingests public filings and public
  posts for aggregate analysis. It does not post, vote, message, or write to any
  external platform.
- **Respect every source's rate limit,** in one place per source, with backoff.
  SEC EDGAR: max 10 req/sec and a descriptive `User-Agent` carrying a real
  contact email.

Start with Phase 0. Before you write code, restate the plan in your own words and flag anything in this brief you think is a mistake.
