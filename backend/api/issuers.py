"""Issuer list endpoint."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.db import ConnDep
from backend.pagination import decode_cursor, encode_cursor

# Router-level prefix and tags, rather than repeating them on every decorator or
# passing them at include_router() time.
router = APIRouter(prefix="/api/v1", tags=["issuers"])

IssuerStatus = Literal["filed", "priced", "listed", "withdrawn"]

# Only filed_at is offered today. hype/quality/gem are cohort-relative and read
# from `scores`, which the worker does not populate until Phase 4 -- offering
# them now would return an arbitrary order that looks authoritative. A client
# asking for one gets a 422 naming the valid values, which is a better failure
# than silently sorted-by-nothing.
IssuerSort = Literal["filed_at"]

# The keyset sort expression, written once so the ORDER BY and the cursor
# predicate can never drift apart -- if they disagree, pagination silently skips
# or repeats rows. first_filed_at is nullable, and '-infinity' is a real
# timestamptz that sorts below every real value, so DESC puts unknown-date
# issuers last instead of first (which is what NULLS FIRST would do by default).
_SORT_KEY = "COALESCE(first_filed_at, '-infinity'::timestamptz)"


class Issuer(BaseModel):
    id: int
    cik: str
    legal_name: str
    ticker: str | None
    exchange: str | None
    sector: str | None
    status: IssuerStatus
    first_filed_at: datetime | None


class PageMeta(BaseModel):
    # None means this is the last page. Clients should test for its presence
    # rather than counting rows against `limit`.
    next_cursor: str | None


class IssuerPage(BaseModel):
    data: list[Issuer]
    meta: PageMeta


_LIST_SQL = f"""
    SELECT id, cik, legal_name, ticker, exchange, sector, status, first_filed_at,
           {_SORT_KEY} AS sort_key
    FROM issuers
    WHERE ($1::text IS NULL OR status = $1)
      AND ($2::timestamptz IS NULL OR ({_SORT_KEY}, id) < ($2, $3::bigint))
    ORDER BY {_SORT_KEY} DESC, id DESC
    LIMIT $4
"""


@router.get(
    "/issuers",
    summary="List issuers, newest filing first",
    responses={400: {"description": "Malformed or mismatched cursor"}},
)
async def list_issuers(
    conn: ConnDep,
    status: Annotated[
        IssuerStatus | None,
        Query(description="Filter to one lifecycle stage."),
    ] = None,
    sort: Annotated[
        IssuerSort,
        Query(description="Sort order. hype/quality/gem arrive in Phase 4."),
    ] = "filed_at",
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Rows per page."),
    ] = 25,
    cursor: Annotated[
        str | None,
        Query(description="Opaque cursor from a previous response's meta.next_cursor."),
    ] = None,
) -> IssuerPage:
    after_key: datetime | None = None
    after_id: int | None = None

    if cursor is not None:
        try:
            payload = decode_cursor(cursor)
            # A cursor is only meaningful for the sort that produced it. Without
            # this check, paging with sort=hype using a filed_at cursor would
            # quietly return the wrong slice rather than an error.
            if payload.get("s") != sort:
                raise ValueError("cursor does not match this sort order")
            after_key = datetime.fromisoformat(payload["k"])
            after_id = int(payload["i"])
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid cursor: {exc}") from exc

    # Fetch one more row than asked for. If it comes back, there is at least one
    # more page -- which is cheaper and more honest than a second COUNT(*) query
    # that would also be racy.
    rows = await conn.fetch(_LIST_SQL, status, after_key, after_id, limit + 1)

    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor = None
    if has_more:
        last = page[-1]
        next_cursor = encode_cursor(
            {
                "s": sort,
                # Read back from the query rather than recomputed here, so the
                # value in the cursor is exactly the one Postgres ordered by.
                "k": last["sort_key"],
                "i": last["id"],
            }
        )

    # asyncpg returns Record objects, which are mapping-like; Pydantic validates
    # each into an Issuer, and anything the model does not declare is dropped
    # before it can reach a client.
    return IssuerPage(
        # sort_key is an internal ordering artefact. Issuer does not declare
        # it, so Pydantic drops it instead of leaking it to clients.
        data=[Issuer(**dict(row)) for row in page],
        meta=PageMeta(next_cursor=next_cursor),
    )
