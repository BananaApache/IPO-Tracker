# IPO Surveillance Platform

Surveillance and anomaly detection over US IPO registrations and the social
chatter around them.

The system ingests upcoming IPO filings from SEC EDGAR and social mentions from
Reddit, links unstructured chatter to **pre-ticker** issuers (the hard part —
these companies have no symbol to search for yet), and scores each issuer on two
independent axes:

- **Hype** — social mention volume and velocity, z-scored across the active cohort.
- **Quality** — revenue growth, margin, leverage, and underwriter tier from the filings.

Two views fall out of the pair: **Hidden Gems** (quality without attention) and
**Overheated** (attention without fundamental support).

> This is a surveillance tool, not a stock recommender. It measures attention and
> reports fundamentals. It does not tell you what to buy.

---

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Skeleton: schema, migrations, `/health` | **current** |
| 1 | Vertical slice: `GET /api/v1/issuers` → Next.js list | not started |
| 2 | EDGAR ingestion worker | not started |
| 3 | Reddit ingestion + entity resolution | not started |
| 4 | Scoring | not started |
| 5 | Dashboard | not started |
| 6 | Hardening + deploy | not started |

---

## Stack

**Backend** — Python 3.13, FastAPI, `asyncpg` with hand-written SQL (no ORM),
numbered `.sql` migrations applied by `backend/migrate.py`, `pydantic-settings`
for config. Workers run as a separate process (Phase 2).

**Frontend** — Next.js 16 App Router, TypeScript, Tailwind (wired up in Phase 1).

**Infra** — Postgres 17 in a container locally, Neon when deployed.

---

## Setup

### 1. Configure

```bash
cp .env.example .env
# edit POSTGRES_PASSWORD
```

`.env` is gitignored. It configures both the Postgres container and the app —
`docker-compose.yml` passes the `POSTGRES_*` vars to the image, and
`backend/config.py` reads the same names via `pydantic-settings`. Compose
overrides `POSTGRES_HOST=db` for the containers; the value in `.env` is the one
your host machine uses.

### 2. Run

```bash
docker compose up --build
```

That starts three things in order:

1. **`db`** — Postgres 17, with a healthcheck so nothing talks to it early.
2. **`migrate`** — one-shot, applies pending migrations, exits 0.
3. **`api`** — FastAPI on `:8000`, gated on `migrate` completing successfully.

### 3. Verify

```bash
curl -s localhost:8000/health | jq
```

```json
{
  "status": "healthy",
  "database": "connected",
  "db_time": "2026-09-05T18:22:41.113204Z",
  "db_version": "17.11"
}
```

The timestamp comes from `SELECT now()` inside Postgres, not from the API
process — so a 200 here means a connection was actually borrowed from the pool
and a query actually ran. If the database is unreachable the endpoint returns
`503`.

Interactive API docs: <http://localhost:8000/docs>

---

## Running without containers

The app is a normal Python project; only Postgres needs to come from somewhere.

```bash
uv sync
# point POSTGRES_HOST/PORT at any Postgres 17
uv run python -m backend.migrate   # apply schema
uv run fastapi dev                 # serve on :8000 with reload
```

`uv run fastapi dev` needs no path argument because the entrypoint is declared
under `[tool.fastapi]` in `pyproject.toml`.

---

## Migrations

Plain SQL in `migrations/`, named `NNN_description.sql`, applied in numeric
order by `backend/migrate.py`.

```bash
uv run python -m backend.migrate
```

The runner records each applied file in a `schema_migrations` table along with a
SHA-256 of its contents, and enforces three rules:

- **Never edit an applied migration.** The checksum comparison catches it and
  refuses to continue, because at that point the file and the live schema
  describe different databases. Write a new numbered file instead.
- **Never backfill a lower number.** If `003` is applied and `002` shows up
  pending, the runner stops — applying it would produce a schema that no fresh
  database could reproduce.
- **One transaction per migration.** Postgres has transactional DDL, so a
  migration that fails halfway leaves nothing behind.

Concurrent runners are serialized with a session advisory lock, so a compose
restart that starts two of them cannot double-apply.

---

## Schema

Ten tables in `migrations/001_initial_schema.sql`. Two decisions there are
load-bearing and worth stating up front:

**`mentions.issuer_id` is nullable.** A mention that could not be resolved to an
issuer is *kept*, with a `match_confidence` and a `needs_review` flag, rather
than dropped. Entity resolution against pre-ticker companies is genuinely hard —
"Circle", "Figure", and "Rivian" are ordinary words, brand names, and ticker-like
strings all at once — so the pipeline is built to be audited. Discarding the
misses would make precision unmeasurable.

**`scores` is precomputed.** The worker writes one snapshot per issuer per day;
the API only ever reads it. Scoring is a cohort-relative operation (a z-score
needs every peer's mention volume), so computing it per request would mean
scanning the cohort on every page load, and two users hitting the same page would
see different numbers.

Money and ratios are `NUMERIC`, never floating point. Status/kind/role columns
are `TEXT` + `CHECK` rather than Postgres `ENUM`, so a later migration can change
the allowed set with an ordinary `ALTER TABLE`.

---

## Layout

```
migrations/          numbered .sql, applied in order
  001_initial_schema.sql
backend/
  main.py            FastAPI app + lifespan (opens/closes the pool)
  config.py          pydantic-settings; builds the DSN
  db.py              asyncpg pool, and the Depends wiring routes use
  migrate.py         migration runner
  api/
    health.py        GET /health
  Dockerfile
frontend/            Next.js 16 (wired up in Phase 1)
docker-compose.yml   db + migrate + api
.env.example         committed; .env is not
```

The pre-FastAPI Django/Celery version of this project lives in git history at
commit `81691fc` and earlier.
