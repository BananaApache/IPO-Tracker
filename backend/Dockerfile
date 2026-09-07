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
# Needed by the managed-platform start command, which applies migrations before
# starting the API. Without this the container builds fine and fails at boot
# with "no such file", which reads like a platform problem rather than a
# missing COPY.
COPY scripts ./scripts
RUN chmod +x ./scripts/release.sh

EXPOSE 8000

# The release script, not `fastapi run` directly. Two reasons:
#
#  * It honours $PORT. An exec-form CMD does no shell expansion, so a hardcoded
#    --port 8000 ignores the port the platform actually scans -- Render reports
#    "no open ports detected" and kills the deploy, with nothing in the log to
#    say why.
#  * It applies migrations first. Managed platforms have no equivalent of
#    compose's `depends_on: service_completed_successfully`.
#
# The worker overrides this with `python -m backend.worker`; compose overrides
# it with `fastapi dev`.
CMD ["./scripts/release.sh"]
