# Deploy checklist

Three platforms: **Neon** (Postgres), **Railway or Render** (API + worker),
**Vercel** (frontend). Work top to bottom; each step's output feeds the next.

Everything below assumes the repo is already pushed to GitHub.

---

## Before you start: two facts that change choices

**A free Render web service sleeps.** It spins down after 15 minutes of
inactivity and takes ~50 seconds to wake. For something people click on at an
event that is disqualifying. Use Render **Starter** ($7/mo) or **Railway Hobby**
($5/mo). Neon's own resume is sub-second and not the problem.

**Keep-warm pings are not worth it on Neon's free tier.** The arithmetic:

| strategy | compute-hours/month | 191.9 budget |
|---|---|---|
| always-on (a held connection) | 720 | **3.8× over** |
| ping every 5 min | 720 | over |
| ping every 10 min | 360 | over |
| ping every 30 min | 120 | within — but the DB is suspended most of the time anyway |

So: **accept the ~1 second cold start.** `KEEP_WARM_MINUTES` exists if you want
it, and the sane use is to set it to `5` for the two or three days around the
event (~72 compute-hours) and back to `0` after. Set `DB_POOL_MIN_SIZE=0` in
production regardless — a held connection stops Neon suspending at all and
burns the budget silently.

---

## 1 · Neon — Postgres

- [ ] Create a project. Region: pick the same continent as your API host.
- [ ] Copy the connection string, then split it into five values — this project
      takes the parts, not the URL. Given
      `postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require`:

      | variable | from the string |
      |---|---|
      | `POSTGRES_USER` | `USER` |
      | `POSTGRES_PASSWORD` | `PASSWORD` |
      | `POSTGRES_HOST` | `HOST` (the part with `-pooler`) |
      | `POSTGRES_PORT` | `5432` |
      | `POSTGRES_DB` | `DBNAME` |
      | `POSTGRES_SSLMODE` | `require` |

- [ ] **`POSTGRES_SSLMODE=require` is not optional on Neon.** The default is
      `prefer`, which works locally because the compose Postgres has no TLS; a
      managed host rejects the plaintext attempt.
- [ ] Use the **pooled** host (it contains `-pooler`). `statement_cache_size=0`
      is already set in `backend/db.py` for exactly this — asyncpg's prepared
      statement cache is unsafe behind a transaction-mode pooler.
- [ ] Nothing to run by hand. Migrations apply on API start via
      `scripts/release.sh`.

## 2 · Render — API + worker

> If a deploy fails with `Connect call failed ('127.0.0.1', 5432)`, the cause is
> always the same: `POSTGRES_HOST` was never set, so the app fell back to its
> localhost default. The error now names the host and says so explicitly.

- [ ] New → **Blueprint** → select the repo. `render.yaml` defines both services.
- [ ] Fill every variable marked `sync: false` in the dashboard — they are
      deliberately absent from the repo. The full list is in §4.
- [ ] `plan: starter` is set on both services. Switching to `free` means a
      50-second wake after 15 minutes idle.
- [ ] Copy the API's public URL; you need it for `CORS_ORIGINS` and Vercel.

## 2b · Railway instead  *(if the trial is active)*

- [ ] New project → Deploy from GitHub repo → select this repo.
- [ ] Service 1, the API. Railway reads `railway.json`, so the Dockerfile and
      start command are already set. Confirm:
      - Root directory: repo root (**not** `backend/`)
      - Health check path: `/health`
- [ ] Service 2, the worker. Add a second service from the same repo, then
      override the start command to `python -m backend.worker` and remove the
      health check (a worker serves no HTTP).
- [ ] Set the variables in §4 on **both** services.
- [ ] Copy the API's public URL. You need it for `CORS_ORIGINS` and Vercel.

### Render instead

- [ ] New → Blueprint → select the repo. `render.yaml` defines both services.
- [ ] Fill every `sync: false` variable in the dashboard (they are deliberately
      not in the repo).
- [ ] Change `plan: starter` to `free` only if you accept the 50-second wake.

## 3 · Vercel — frontend

- [ ] New Project → import the repo.
- [ ] **Root directory: `frontend`.** This matters; the repo root is a Python
      project and the build will fail without it.
- [ ] Framework preset: Next.js. Build command and output are in
      `frontend/vercel.json`.
- [ ] One variable: `API_BASE_URL` = the Railway/Render API URL, no trailing
      slash.
- [ ] Copy the deployment URL, then go back and set `CORS_ORIGINS` on the API
      to `["https://your-app.vercel.app"]` and redeploy the API.

---

## 4 · Environment variables

### Railway / Render — API service

| variable | value | notes |
|---|---|---|
| `POSTGRES_HOST` | Neon pooled host | contains `-pooler` |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | from Neon | |
| `POSTGRES_USER` | from Neon | |
| `POSTGRES_PASSWORD` | from Neon | |
| `POSTGRES_SSLMODE` | `require` | Neon rejects plaintext |
| `DB_POOL_MIN_SIZE` | `0` | a held connection defeats autosuspend |
| `DB_POOL_MAX_SIZE` | `5` | keep well under Neon's ceiling |
| `CORS_ORIGINS` | `["https://your-app.vercel.app"]` | JSON list, no trailing slash |
| `MENTION_HASH_SALT` | `python -c "import secrets;print(secrets.token_hex(32))"` | **write-once** — changing it orphans every stored hash |
| `SEC_USER_AGENT` | `YourProject/1.0 (you@example.com)` | EDGAR 403s without a real contact |
| `RATE_LIMIT_REQUESTS` | `60` | optional; per IP per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | optional |

### Railway / Render — worker service

Everything above **except** `CORS_ORIGINS` and the rate-limit pair, plus:

| variable | value | notes |
|---|---|---|
| `MARKET_DATA_API_KEY` | Polygon key | worker only — the API never calls Polygon |
| `NEWS_API_KEY` | Finnhub key | worker only |
| `DB_POOL_MAX_SIZE` | `3` | it shares Neon with the API |
| `KEEP_WARM_MINUTES` | `0` | `5` for a demo window, then back to `0` |
| `SEC_LOOKBACK_DAYS` | `7` | ongoing poll window |
| `SOCIAL_LOOKBACK_DAYS` | `3` | |

### Vercel — frontend

| variable | value | notes |
|---|---|---|
| `API_BASE_URL` | the API's public URL | **no `NEXT_PUBLIC_` prefix** |

---

## 5 · Key exposure — verified, not assumed

- **No `NEXT_PUBLIC_` variable exists anywhere in this project.** Next.js only
  inlines `NEXT_PUBLIC_*` into the browser bundle, so nothing here can reach a
  client.
- `MARKET_DATA_API_KEY` and `NEWS_API_KEY` appear in exactly three files:
  `backend/config.py`, `backend/prices/polygon.py`, and `.env.example` (as
  placeholders). **Zero references in the frontend.**
- `API_BASE_URL` is read in `frontend/lib/api.ts`, which is imported only by
  Server Components. The browser talks to Vercel; only Vercel talks to the API.
- The API cannot spend money at a data provider. Polygon and Finnhub are called
  only by the worker, whose own limiter caps it at the free tier's rate no matter
  what public traffic does.

## 6 · After it is live

- [ ] `curl https://your-api/health` → `{"status":"healthy",...}` with a real
      `db_time`
- [ ] `curl https://your-api/api/v1/stats` → non-zero counts
- [ ] Open the Vercel URL. The overview should show the three accuracy figures.
- [ ] Hit the API 70 times in a minute; the last few should return `429`.
- [ ] Check the worker's logs for `worker up:` and one completed ingest cycle.

## 7 · Costs

| | plan | monthly |
|---|---|---|
| Neon | Free | $0 |
| Railway | Hobby | ~$5 |
| Vercel | Hobby | $0 |
| Polygon | Free | $0 |
| Finnhub | Free | $0 |

Render Starter instead of Railway is ~$7 per service, so ~$14 for API + worker.
Railway's single $5 covers both.
