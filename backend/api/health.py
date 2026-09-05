"""Liveness + database reachability."""

from datetime import datetime
from typing import Literal

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import PoolDep

# No /api/v1 prefix: /health is infrastructure, not product API. Load balancers
# and compose healthchecks should not have to track an API version.
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    database: Literal["connected"]
    # Read from Postgres, not from this process's clock. A hardcoded timestamp
    # would let the endpoint report healthy with the database on fire.
    db_time: datetime
    db_version: str


@router.get(
    "/health",
    summary="Liveness check with a real database round-trip",
    responses={503: {"description": "Database unreachable"}},
)
async def health(pool: PoolDep) -> HealthResponse:
    # Takes the pool rather than a connection, because acquiring the connection
    # here is what lets a pool-exhaustion or connection failure surface as 503
    # instead of an unhandled 500 raised inside a dependency.
    try:
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT now() AS db_time, current_setting('server_version') AS db_version"
            )
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="database unreachable") from exc

    return HealthResponse(
        status="healthy",
        database="connected",
        db_time=row["db_time"],
        db_version=row["db_version"],
    )
