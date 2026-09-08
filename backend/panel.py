"""Daily attention panel: does mention volume move with next-day return?

    uv run python -m backend.panel

This is the design that repo uses -- daily sentiment/volume against daily
returns -- and it is far better powered than the event study, for a structural
reason worth stating: the event study gives each listing ONE attention
observation, so n is the number of listings (188, of which 6 had any attention).
A panel gives one observation per issuer-day, so n is issuers x days.

Parameters fixed in advance, as with the event study:
  * attention = accepted mentions posted on that calendar day
  * outcome   = next trading day's close-to-close return
  * test      = Spearman on the pooled panel, plus per-issuer to check that a
                pooled result is not one issuer's story
"""

import asyncio
from dataclasses import dataclass

import asyncpg

from backend.config import get_settings
from backend.study import permutation_p, spearman

MIN_ISSUER_DAYS = 20   # below this an issuer's own correlation is noise


@dataclass
class Row:
    issuer: str
    ticker: str
    day: str
    mentions: int
    engagement: int
    next_return: float


_PANEL = """
    WITH daily AS (
        SELECT issuer_id, posted_at::date AS day,
               count(*) AS mentions, sum(engagement_score) AS engagement
        FROM mentions
        WHERE NOT needs_review AND issuer_id IS NOT NULL
        GROUP BY 1, 2
    ),
    bars AS (
        SELECT issuer_id, day, close,
               lead(close) OVER (PARTITION BY issuer_id ORDER BY day) AS next_close
        FROM price_bars WHERE close IS NOT NULL AND close > 0
    )
    SELECT i.legal_name, i.ticker, d.day, d.mentions, d.engagement,
           b.next_close / b.close - 1 AS next_return
    FROM daily d
    JOIN bars b ON b.issuer_id = d.issuer_id AND b.day = d.day
    JOIN issuers i ON i.id = d.issuer_id
    WHERE b.next_close IS NOT NULL
    ORDER BY i.legal_name, d.day
"""


async def load() -> list[Row]:
    settings = get_settings()
    connection = await asyncpg.connect(settings.database_dsn, statement_cache_size=0)
    try:
        return [
            Row(r["legal_name"], r["ticker"] or "?", str(r["day"]),
                r["mentions"], int(r["engagement"] or 0), float(r["next_return"]))
            for r in await connection.fetch(_PANEL)
        ]
    finally:
        await connection.close()


def report(rows: list[Row]) -> None:
    issuers = {r.ticker for r in rows}
    print(f"panel observations (issuer-days with both a mention count and a return): {len(rows)}")
    print(f"distinct issuers: {len(issuers)}")
    if len(rows) < 30:
        print("\nTOO FEW OBSERVATIONS to test. The panel needs price bars for the "
              "issuers that actually get discussed.")
        return

    for label, xs in (("mention count", [float(r.mentions) for r in rows]),
                      ("engagement sum", [float(r.engagement) for r in rows])):
        ys = [r.next_return for r in rows]
        rho = spearman(xs, ys)
        p = permutation_p(xs, ys, rho)
        print(f"\npooled: {label} vs next-day return")
        print(f"   n={len(rows)}   Spearman rho={rho:+.4f}   permutation p={p:.4f}")

    print("\nper-issuer, so a pooled result is not one name's story:")
    print(f"{'ticker':<9}{'days':>6}{'rho':>9}{'median ret':>12}  issuer")
    print("-" * 62)
    shown = 0
    for ticker in sorted(issuers):
        sub = [r for r in rows if r.ticker == ticker]
        if len(sub) < MIN_ISSUER_DAYS:
            continue
        xs = [float(r.mentions) for r in sub]
        ys = [r.next_return for r in sub]
        rho = spearman(xs, ys)
        med = sorted(ys)[len(ys) // 2]
        print(f"{ticker:<9}{len(sub):>6}{rho:>+9.3f}{med:>11.2%}  {sub[0].issuer[:26]}")
        shown += 1
    if not shown:
        print(f"   no issuer has {MIN_ISSUER_DAYS}+ panel days yet")


if __name__ == "__main__":
    report(asyncio.run(load()))
