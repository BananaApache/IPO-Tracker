"""FastAPI application entrypoint.

Run it with:  uv run fastapi dev
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.health import router as health_router
from backend.api.issuers import router as issuers_router
from backend.config import get_settings
from backend.db import create_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Owns anything whose lifetime is the process, not the request.

    Code before `yield` runs once at startup, code after runs once at shutdown.
    The pool is created here rather than at import time so that importing this
    module -- which tests and tooling do -- never opens a socket.
    """
    settings = get_settings()
    app.state.pool = await create_pool(settings)
    try:
        yield
    finally:
        # Waits for in-flight queries, then closes every connection. Without
        # this, a reload leaks server-side backends until Postgres refuses new
        # ones.
        await app.state.pool.close()


app = FastAPI(
    title="IPO Surveillance Platform",
    version="0.1.0",
    summary=(
        "Surveillance and anomaly detection over pre-listing IPO registrations "
        "and the social chatter around them. Not a recommender."
    ),
    lifespan=lifespan,
)

# CORS is a *browser* mechanism. A browser refuses to hand a cross-origin
# response back to page JavaScript unless the server opted in with these
# headers; nothing else -- curl, a Python client, or a Next.js server component
# fetching from Node -- is affected, because the restriction lives in the
# browser, not in HTTP.
#
# So this middleware does nothing for the Phase 1 page, whose fetch happens on
# the server. It is here because Phase 5 polls this API from the browser with
# TanStack Query, and that request is exactly the case CORS governs.
#
# Origins come from settings and are listed explicitly. allow_origins=["*"] is
# the reflex fix and the wrong one: it cannot be combined with credentials, and
# it invites any page on the internet to read this API using a visitor's
# browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(issuers_router)
