# Build context is the repo root (see docker-compose.yml), because the
# dependency manifests live there alongside the `backend` package.
FROM python:3.13-slim

# uv resolves and installs from the committed lockfile, so the image gets the
# same versions the host does.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Manifests first, as their own layer: dependency install is the slow step and
# it should not re-run every time application code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY backend ./backend
COPY migrations ./migrations

EXPOSE 8000

CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000"]
