"""Tier A: the full US IPO census from Finnhub's IPO calendar.

This is the denominator. It is not the interesting half of the module, but it is
the half that lets any claim about "IPOs" be checked, and it is cheap: one
source, one request per month window, no per-company fan-out.

Two rules shape the code:

  * Raw first. Every window's response is written to `data/raw/finnhub_ipo/`
    before anything is normalized, and the normalize step reads only those
    files. So the counts in the analysis are reproducible with the network off,
    and a change in Finnhub's response shape shows up as a diff rather than as
    a number that quietly moved.
  * Nothing is dropped silently. De-duplication and junk filtering both write
    their reasons to `census_dropped.csv`. A census whose exclusions are
    invisible is not a census.

Run:  uv run --group research python -m research.collect.finnhub_census
      uv run --group research python -m research.collect.finnhub_census --normalize-only
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, date, datetime
from typing import Any

from backend.http import RetryingClient
from research.collect.config import get_research_settings
from research.collect.paths import (
    CENSUS_DROPPED_CSV,
    CENSUS_PARQUET,
    RAW_FINNHUB,
    ensure_dirs,
)

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://finnhub.io/api/v1/calendar/ipo"
START = date(2019, 1, 1)

# Statuses Finnhub returns. `withdrawn` is kept deliberately: those are
# companies that filed and never listed, which is the comparison group the
# module would otherwise have had to hand-build.
KNOWN_STATUSES = frozenset({"expected", "priced", "withdrawn", "filed"})


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive month windows covering [start, end].

    Month at a time rather than one long span: the endpoint returns a truncated
    array for multi-year ranges without saying so, which is the failure mode
    that silently shortens a census.
    """
    windows = []
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
               else cur.replace(month=cur.month + 1))
        windows.append((cur, min(nxt.replace(day=1), end) if nxt > end else
                        date.fromordinal(nxt.toordinal() - 1)))
        cur = nxt
    return windows


async def fetch_windows(start: date, end: date, *, refetch: bool = False) -> int:
    """Cache one raw JSON file per month window. Returns the number fetched."""
    ensure_dirs()
    s = get_research_settings()
    if not s.finnhub_key:
        raise SystemExit("NEWS_API_KEY (Finnhub) is not set; cannot build the census.")

    windows = month_windows(start, end)
    todo = []
    for a, b in windows:
        path = RAW_FINNHUB / f"{a.isoformat()}_{b.isoformat()}.json"
        # A window already on disk is never re-requested. That is what makes the
        # run resumable: kill it and start it again and it picks up where it
        # stopped, at no quota cost for what it already has.
        if refetch or not path.exists():
            todo.append((a, b, path))

    logger.info("census: %d windows total, %d to fetch", len(windows), len(todo))
    if not todo:
        return 0

    client = RetryingClient(user_agent=s.sec_user_agent, per_second=s.finnhub_per_second)
    fetched = 0
    try:
        for a, b, path in todo:
            payload = await client.get_json(
                CALENDAR_URL,
                params={"from": a.isoformat(), "to": b.isoformat(), "token": s.finnhub_key},
            )
            rows = payload.get("ipoCalendar")
            if rows is None:
                raise SystemExit(
                    f"census: unexpected response for {a}..{b}: {str(payload)[:200]}"
                )
            # The retrieval timestamp travels with the data, not in a separate
            # log: a census row's provenance is part of the row.
            path.write_text(json.dumps(
                {"from": a.isoformat(), "to": b.isoformat(),
                 "fetched_at": datetime.now(UTC).isoformat(),
                 "ipoCalendar": rows}, indent=1) + "\n")
            fetched += 1
            logger.info("census: %s..%s -> %d rows", a, b, len(rows))
    finally:
        await client.aclose()
    return fetched


def _clean_price(raw: Any) -> tuple[float | None, float | None, str | None]:
    """Finnhub's `price` is a number, a range string, or null.

    Returns (low, high, as_returned). The original string is kept because the
    module brief needs to reconcile it against the first actual trade, and a
    parsed midpoint would destroy the evidence of which it was.
    """
    if raw is None or raw == "":
        return None, None, None
    text = str(raw).strip()
    parts = [p.strip() for p in text.split("-") if p.strip()]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None, None, text
    if not nums:
        return None, None, text
    return min(nums), max(nums), text


def normalize() -> tuple[int, int]:
    """Read every cached window, de-duplicate, write census + dropped ledger."""
    import pandas as pd

    files = sorted(RAW_FINNHUB.glob("*.json"))
    if not files:
        raise SystemExit("no cached windows; run without --normalize-only first.")

    records: list[dict[str, Any]] = []
    for path in files:
        blob = json.loads(path.read_text())
        for row in blob.get("ipoCalendar") or []:
            low, high, as_returned = _clean_price(row.get("price"))
            records.append({
                "name": (row.get("name") or "").strip(),
                "symbol": (row.get("symbol") or "").strip().upper() or None,
                "exchange": (row.get("exchange") or "").strip() or None,
                "status": (row.get("status") or "").strip().lower() or None,
                "calendar_date": row.get("date"),
                "shares": row.get("numberOfShares"),
                "total_shares_value": row.get("totalSharesValue"),
                "price_low": low,
                "price_high": high,
                "price_as_returned": as_returned,
                "window_file": path.name,
                "fetched_at": blob.get("fetched_at"),
            })

    df = pd.DataFrame.from_records(records)
    df["calendar_date"] = pd.to_datetime(df["calendar_date"], errors="coerce").dt.date
    dropped: list[dict[str, Any]] = []

    def drop(mask, reason: str) -> None:
        nonlocal df
        if mask.any():
            out = df[mask].copy()
            out["drop_reason"] = reason
            dropped.append(out)
            df = df[~mask].copy()

    drop(df["name"].eq("") & df["symbol"].isna(), "no name and no symbol")
    drop(df["calendar_date"].isna(), "unparseable calendar date")
    drop(~df["status"].isin(KNOWN_STATUSES), "status outside the documented set")

    # Windows overlap at month edges and Finnhub restates rows as a deal moves
    # from filed to priced, so the same issuer legitimately appears more than
    # once. Key on symbol+date where a symbol exists and name+date otherwise --
    # a bare name is not unique enough on its own, but for the ~40% of rows
    # with no symbol it is all there is.
    df["dedupe_key"] = df.apply(
        lambda r: f"sym:{r['symbol']}|{r['calendar_date']}" if r["symbol"]
        else f"name:{r['name'].casefold()}|{r['calendar_date']}", axis=1)
    # Keep the most informative duplicate rather than the first: a `priced` row
    # carries a final price and share count that the earlier `filed` row does not.
    status_rank = {"priced": 0, "expected": 1, "filed": 2, "withdrawn": 3}
    df["_rank"] = df["status"].map(status_rank).fillna(9)
    df = df.sort_values(["dedupe_key", "_rank", "fetched_at"])
    dupe_mask = df.duplicated("dedupe_key", keep="first")
    drop(dupe_mask, "duplicate of a higher-information row for the same symbol+date")
    df = df.drop(columns=["_rank"])

    df = df.sort_values(["calendar_date", "name"]).reset_index(drop=True)
    df.to_parquet(CENSUS_PARQUET, index=False)

    if dropped:
        pd.concat(dropped, ignore_index=True).to_csv(CENSUS_DROPPED_CSV, index=False)
        n_dropped = sum(len(d) for d in dropped)
    else:
        CENSUS_DROPPED_CSV.write_text("drop_reason\n")
        n_dropped = 0

    logger.info("census: %d rows kept, %d dropped -> %s", len(df), n_dropped, CENSUS_PARQUET)
    return len(df), n_dropped


async def _amain() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=lambda v: date.fromisoformat(v), default=START)
    ap.add_argument("--end", type=lambda v: date.fromisoformat(v),
                    default=datetime.now(UTC).date())
    ap.add_argument("--normalize-only", action="store_true",
                    help="rebuild the census from cached raw windows, no network")
    ap.add_argument("--refetch", action="store_true",
                    help="re-request windows already on disk")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    if not args.normalize_only:
        await fetch_windows(args.start, args.end, refetch=args.refetch)
    kept, dropped = normalize()
    print(f"census: {kept} rows, {dropped} dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
