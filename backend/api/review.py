"""Manual review of low-confidence matches."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from backend.db import ConnDep
from backend.pagination import decode_cursor, encode_cursor

router = APIRouter(prefix="/api/v1/review", tags=["review"])

_SORT = "mentions"  # cursor namespace, so a review cursor cannot be replayed elsewhere


class ReviewItem(BaseModel):
    mention_id: int
    source: str
    url: str | None
    title: str | None
    body_excerpt: str | None
    posted_at: datetime
    proposed_issuer_id: int
    proposed_issuer_name: str
    matched_alias: str | None
    match_confidence: float | None


class PageMeta(BaseModel):
    next_cursor: str | None


class ReviewPage(BaseModel):
    data: list[ReviewItem]
    meta: PageMeta


class ReviewDecision(BaseModel):
    # confirm: the proposed issuer is right. reject: it is not this issuer.
    decision: Literal["confirm", "reject"]
    note: str | None = Field(default=None, max_length=500)


class ReviewResult(BaseModel):
    mention_id: int
    issuer_id: int | None
    needs_review: bool
    match_confidence: float | None


_QUEUE_SQL = """
    SELECT m.id, m.source, m.url, m.title, m.body_excerpt, m.posted_at,
           m.issuer_id, i.legal_name, a.alias, m.match_confidence
    FROM mentions m
    JOIN issuers i ON i.id = m.issuer_id
    LEFT JOIN aliases a ON a.id = m.matched_alias_id
    WHERE m.needs_review
      AND ($1::bigint IS NULL OR m.id < $1)
    ORDER BY m.id DESC
    LIMIT $2
"""


@router.get("/queue", summary="Low-confidence matches awaiting a human decision")
async def review_queue(
    conn: ConnDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query()] = None,
) -> ReviewPage:
    after_id: int | None = None
    if cursor is not None:
        try:
            payload = decode_cursor(cursor)
            if payload.get("s") != _SORT:
                raise ValueError("cursor does not match this listing")
            after_id = int(payload["i"])
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid cursor: {exc}") from exc

    # Ordered by id DESC, which for an append-only table is newest-first and
    # needs no extra index -- the primary key already provides it.
    rows = await conn.fetch(_QUEUE_SQL, after_id, limit + 1)
    has_more = len(rows) > limit
    page = rows[:limit]

    return ReviewPage(
        data=[
            ReviewItem(
                mention_id=r["id"], source=r["source"], url=r["url"], title=r["title"],
                body_excerpt=r["body_excerpt"], posted_at=r["posted_at"],
                proposed_issuer_id=r["issuer_id"], proposed_issuer_name=r["legal_name"],
                matched_alias=r["alias"],
                match_confidence=float(r["match_confidence"]) if r["match_confidence"] is not None else None,
            )
            for r in page
        ],
        meta=PageMeta(
            next_cursor=encode_cursor({"s": _SORT, "i": page[-1]["id"]}) if has_more else None
        ),
    )


@router.post(
    "/{mention_id}",
    summary="Confirm or reject a proposed match",
    responses={404: {"description": "No such mention awaiting review"}},
)
async def submit_review(
    conn: ConnDep,
    decision: ReviewDecision,
    mention_id: Annotated[int, Path(ge=1)],
) -> ReviewResult:
    # A rejection clears issuer_id and the alias link but KEEPS the row. That is
    # the point of the nullable issuer_id: a human-confirmed non-match is the
    # most valuable label in the system, and deleting it would throw away the
    # only ground truth this pipeline ever generates.
    row = await conn.fetchrow(
        """
        UPDATE mentions SET
            issuer_id        = CASE WHEN $2 THEN issuer_id ELSE NULL END,
            matched_alias_id = CASE WHEN $2 THEN matched_alias_id ELSE NULL END,
            match_confidence = CASE WHEN $2 THEN 1.00 ELSE 0.00 END,
            needs_review     = FALSE
        WHERE id = $1 AND needs_review
        RETURNING id, issuer_id, needs_review, match_confidence
        """,
        mention_id,
        decision.decision == "confirm",
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no such mention awaiting review")

    return ReviewResult(
        mention_id=row["id"], issuer_id=row["issuer_id"], needs_review=row["needs_review"],
        match_confidence=float(row["match_confidence"]) if row["match_confidence"] is not None else None,
    )
