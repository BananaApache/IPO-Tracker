"""Connection pool wiring.

Mental model, since this is the piece that differs most from Django's ORM:

Opening a Postgres connection is expensive -- a TCP handshake, authentication,
and a server-side backend process. You do not want that on the request path, and
Postgres cannot afford thousands of them anyway. So the process opens a small
fixed set of connections once at startup and keeps them; a request *borrows* one
for the duration of its query and returns it immediately. That set is the pool.

The pool lives on `app.state` because it belongs to the application's lifetime,
not to any request. Routes reach it through `Depends`, which is FastAPI's way of
saying "before you run this handler, call this function and pass me the result."
That indirection is what makes the pool swappable in tests without the route
knowing.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, Request

from backend.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_dsn,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # asyncpg caches a server-side prepared statement per unique query.
        # That is a correctness hazard behind a connection pooler in
        # transaction mode (PgBouncer), which is what Neon fronts you with, so
        # cap the cache now rather than debugging it after deploy.
        statement_cache_size=0,
    )


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]


async def get_connection(pool: PoolDep) -> AsyncIterator[asyncpg.Connection]:
    """Borrow one pooled connection for the life of a request.

    A dependency that `yield`s runs its teardown after the response is built,
    so the connection always goes back to the pool -- including when the
    handler raised.
    """
    async with pool.acquire() as connection:
        yield connection


ConnDep = Annotated[asyncpg.Connection, Depends(get_connection)]
