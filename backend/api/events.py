"""Listing events and pipeline summary, for the dashboard."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db import ConnDep
from backend.pagination import decode_cursor, encode_cursor

router = APIRouter(prefix="/api/v1", tags=["events"])

Cohort = Literal["operating", "spac", "etf_or_trust", "re_listing"]
Status = Literal["listed", "awaiting_ticker", "registered_no_trade", "no_price_data"]


class ListingEvent(BaseModel):
    issuer_id: int
    legal_name: str
    ticker: str | None
    exchange: str | None
    sector: str | None
    cohort: Cohort
    cohort_reason: str | None
    status: Status
    eight_a_filed_at: datetime
    listed_on: date | None
    open_price: Decimal | None
    ipo_price: Decimal | None
    # open/ipo - 1, when both are known. Computed, never stored.
    day_one_pop: float | None
    bars: int
    mentions: int


class PageMeta(BaseModel):
    next_cursor: str | None


class EventPage(BaseModel):
    data: list[ListingEvent]
    meta: PageMeta


_EVENTS_SQL = """
    SELECT e.issuer_id, i.legal_name, e.ticker, e.exchange, i.sector,
           e.cohort, e.cohort_reason, e.status, e.eight_a_filed_at,
           e.listed_on, e.open_price, e.ipo_price,
           (SELECT count(*) FROM price_bars b WHERE b.issuer_id = e.issuer_id) AS bars,
           (SELECT count(*) FROM mentions m
             WHERE m.issuer_id = e.issuer_id AND NOT m.needs_review) AS mentions
    FROM listing_events e
    JOIN issuers i ON i.id = e.issuer_id
    WHERE ($1::text IS NULL OR e.cohort = $1)
      AND ($2::text IS NULL OR e.status = $2)
      AND ($3::timestamptz IS NULL OR (e.eight_a_filed_at, e.issuer_id) < ($3, $4::bigint))
    ORDER BY e.eight_a_filed_at DESC, e.issuer_id DESC
    LIMIT $5
"""


@router.get("/events", summary="Detected listing events with cohort classification")
async def list_events(
    conn: ConnDep,
    cohort: Annotated[Cohort | None, Query()] = None,
    status: Annotated[Status | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> EventPage:
    after_at = after_id = None
    if cursor is not None:
        try:
            payload = decode_cursor(cursor)
            if payload.get("s") != "events":
                raise ValueError("cursor does not match this listing")
            after_at = datetime.fromisoformat(payload["k"])
            after_id = int(payload["i"])
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid cursor: {exc}") from exc

    rows = await conn.fetch(_EVENTS_SQL, cohort, status, after_at, after_id, limit + 1)
    has_more = len(rows) > limit
    page = rows[:limit]

    def pop(row) -> float | None:
        if row["open_price"] is None or not row["ipo_price"]:
            return None
        return round(float(row["open_price"]) / float(row["ipo_price"]) - 1, 4)

    return EventPage(
        data=[ListingEvent(**dict(r), day_one_pop=pop(r)) for r in page],
        meta=PageMeta(
            next_cursor=encode_cursor(
                {"s": "events", "k": page[-1]["eight_a_filed_at"], "i": page[-1]["issuer_id"]}
            )
            if has_more
            else None
        ),
    )


class Accuracy(BaseModel):
    name: str
    metric: str
    value: str
    basis: str
    caveat: str


class Stats(BaseModel):
    issuers: int
    filings: int
    aliases: int
    offerings: int
    offerings_with_price: int
    offerings_with_underwriters: int
    listing_events: int
    events_by_cohort: dict[str, int]
    events_by_status: dict[str, int]
    study_cohort: int
    price_bars: int
    mentions: int
    mentions_needing_review: int
    accuracy: list[Accuracy]


@router.get("/stats", summary="Pipeline counts and measured accuracy")
async def stats(conn: ConnDep) -> Stats:
    one = await conn.fetchrow(
        """
        SELECT (SELECT count(*) FROM issuers)        AS issuers,
               (SELECT count(*) FROM filings)        AS filings,
               (SELECT count(*) FROM aliases)        AS aliases,
               (SELECT count(*) FROM offerings)      AS offerings,
               (SELECT count(*) FROM offerings WHERE price_disclosure='disclosed') AS with_price,
               (SELECT count(DISTINCT offering_id) FROM offering_underwriters)      AS with_uw,
               (SELECT count(*) FROM listing_events) AS events,
               (SELECT count(*) FROM study_cohort)   AS study,
               (SELECT count(*) FROM price_bars)     AS bars,
               (SELECT count(*) FROM mentions)       AS mentions,
               (SELECT count(*) FROM mentions WHERE needs_review) AS review
        """
    )
    cohorts = {r["cohort"]: r["n"] for r in await conn.fetch(
        "SELECT cohort, count(*) AS n FROM listing_events GROUP BY 1")}
    statuses = {r["status"]: r["n"] for r in await conn.fetch(
        "SELECT status, count(*) AS n FROM listing_events GROUP BY 1")}

    # Hard-coded because they are measurements, not live queries. Each was
    # produced by a hand-labelled evaluation recorded in docs/.
    accuracy = [
        Accuracy(
            name="Prospectus extraction", metric="price accuracy", value="75%",
            basis="12 hand-labelled filings, held out, scored once before tuning",
            caveat="92% after fixing what the first run exposed, on a set that is no longer clean",
        ),
        Accuracy(
            name="Entity resolution", metric="precision", value="0.70",
            basis="100 of 2,072 accepted matches sampled from 1,004,502 Hacker News items",
            caveat="recall is unbounded between 0.11 and 0.84; a 40-item reject sample cannot narrow it",
        ),
        Accuracy(
            name="Event detection", metric="recall", value="0.99",
            basis="160 of 162 priced IPOs on Finnhub's calendar detected independently",
            caveat="precision 0.79; our definition is stricter than Finnhub's, which counts uplistings",
        ),
    ]

    return Stats(
        issuers=one["issuers"], filings=one["filings"], aliases=one["aliases"],
        offerings=one["offerings"], offerings_with_price=one["with_price"],
        offerings_with_underwriters=one["with_uw"], listing_events=one["events"],
        events_by_cohort=cohorts, events_by_status=statuses, study_cohort=one["study"],
        price_bars=one["bars"], mentions=one["mentions"],
        mentions_needing_review=one["review"], accuracy=accuracy,
    )
