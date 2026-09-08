"""The event study: does attention around a listing predict its return?

    uv run python -m backend.study

EVERY PARAMETER IS FIXED HERE, IN ADVANCE. The window is ±14 days, the horizons
are 30/60/90 calendar days, the attention metric is a count of accepted mentions,
and the test is a Spearman rank correlation. None of these will be adjusted
after seeing a result -- doing so is how a null becomes a finding that does not
replicate.

A null result is the expected outcome and a publishable one.

Method notes:

* Returns are computed from `price_bars` on read, never stored, so a published
  figure can always be re-derived from the series it came from.
* The baseline is `open_price` -- the first traded price, not the 424B4 price.
  Only a minority of events have an extracted IPO price, and the thesis is about
  what a buyer at open experienced.
* Spearman rather than Pearson: IPO returns are heavy-tailed and attention counts
  are heavily skewed, so a rank correlation is the honest choice. Significance
  comes from a permutation test rather than a t-approximation, because the
  sample is small and the normal approximation is unreliable there.
"""

import asyncio
import random
from dataclasses import dataclass
from datetime import date, timedelta

import asyncpg

from backend.config import get_settings

# --- fixed in advance -------------------------------------------------------
ATTENTION_WINDOW_DAYS = 14
HORIZONS = (30, 60, 90)
PERMUTATIONS = 10_000
SEED = 20260907
# ---------------------------------------------------------------------------


@dataclass
class Observation:
    ticker: str
    name: str
    listed_on: date
    open_price: float
    attention: int
    returns: dict[int, float | None]


_COHORT = """
    SELECT e.issuer_id, e.ticker, i.legal_name, e.listed_on, e.open_price
    FROM study_cohort e JOIN issuers i ON i.id = e.issuer_id
    WHERE e.open_price IS NOT NULL AND e.open_price > 0
    ORDER BY e.listed_on
"""

# Accepted matches only. A needs_review row is an unconfirmed proposal, and
# counting it would put the matcher's uncertainty into the independent variable.
_ATTENTION = """
    SELECT count(*) FROM mentions
    WHERE issuer_id = $1 AND NOT needs_review
      AND posted_at >= $2 AND posted_at < $3
"""

# First close at or after the horizon date. Calendar horizons land on weekends
# and holidays, so the next available session is used rather than skipping the
# event -- skipping would drop exactly the events that listed before a holiday.
_CLOSE_AT = """
    SELECT close FROM price_bars
    WHERE issuer_id = $1 AND day >= $2 AND close IS NOT NULL
    ORDER BY day LIMIT 1
"""

_LAST_BAR = "SELECT max(day) FROM price_bars WHERE issuer_id = $1"


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, so ties do not distort the correlation."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return 0.0 if dx == 0 or dy == 0 else num / (dx * dy)


def spearman(xs: list[float], ys: list[float]) -> float:
    return _pearson(_ranks(xs), _ranks(ys))


def permutation_p(xs: list[float], ys: list[float], observed: float) -> float:
    """Two-sided p from shuffling the labels. No distributional assumption."""
    rng = random.Random(SEED)
    rx, ry = _ranks(xs), _ranks(ys)
    shuffled = list(ry)
    extreme = 0
    for _ in range(PERMUTATIONS):
        rng.shuffle(shuffled)
        if abs(_pearson(rx, shuffled)) >= abs(observed):
            extreme += 1
    return (extreme + 1) / (PERMUTATIONS + 1)


async def collect() -> list[Observation]:
    settings = get_settings()
    connection = await asyncpg.connect(settings.database_dsn, statement_cache_size=0)
    try:
        rows = await connection.fetch(_COHORT)
        out: list[Observation] = []
        for row in rows:
            listed = row["listed_on"]
            attention = await connection.fetchval(
                _ATTENTION,
                row["issuer_id"],
                listed - timedelta(days=ATTENTION_WINDOW_DAYS),
                listed + timedelta(days=ATTENTION_WINDOW_DAYS + 1),
            )
            last = await connection.fetchval(_LAST_BAR, row["issuer_id"])
            open_price = float(row["open_price"])

            returns: dict[int, float | None] = {}
            for horizon in HORIZONS:
                target = listed + timedelta(days=horizon)
                # Only count a horizon as available when the series actually
                # extends past it; otherwise the "return" is just the last
                # price we happen to have, which biases toward recent listings.
                if last is None or last < target:
                    returns[horizon] = None
                    continue
                close = await connection.fetchval(_CLOSE_AT, row["issuer_id"], target)
                returns[horizon] = (
                    None if close is None else float(close) / open_price - 1
                )

            out.append(
                Observation(
                    ticker=row["ticker"] or "?", name=row["legal_name"],
                    listed_on=listed, open_price=open_price,
                    attention=attention, returns=returns,
                )
            )
        return out
    finally:
        await connection.close()


def report(observations: list[Observation]) -> None:
    print(f"study cohort with an opening price: {len(observations)}")
    with_attention = [o for o in observations if o.attention > 0]
    print(f"of which any attention in +/-{ATTENTION_WINDOW_DAYS}d: {len(with_attention)}")
    print(f"attention window: +/-{ATTENTION_WINDOW_DAYS} days   horizons: {HORIZONS}")
    print(f"metric: accepted mentions   test: Spearman, {PERMUTATIONS:,}-permutation p\n")

    print(f"{'horizon':<9}{'n':>5}{'n>0 att':>9}{'rho':>8}{'p':>8}"
          f"{'med ret':>10}{'med hi-att':>12}{'med lo-att':>12}")
    print("-" * 73)
    for horizon in HORIZONS:
        pairs = [(o.attention, o.returns[horizon]) for o in observations
                 if o.returns[horizon] is not None]
        n = len(pairs)
        nonzero = sum(1 for a, _ in pairs if a > 0)
        if n < 3 or nonzero == 0:
            print(f"{horizon:<9}{n:>5}{nonzero:>9}{'--':>8}{'--':>8}"
                  f"{'--':>10}{'--':>12}{'--':>12}")
            continue
        xs = [float(a) for a, _ in pairs]
        ys = [r for _, r in pairs]
        rho = spearman(xs, ys)
        p = permutation_p(xs, ys, rho)
        med = sorted(ys)[n // 2]
        cut = sorted(xs)[n // 2]
        hi = sorted(r for a, r in pairs if a > cut) or [float("nan")]
        lo = sorted(r for a, r in pairs if a <= cut) or [float("nan")]
        print(f"{horizon:<9}{n:>5}{nonzero:>9}{rho:>8.3f}{p:>8.3f}"
              f"{med:>9.1%}{hi[len(hi)//2]:>12.1%}{lo[len(lo)//2]:>12.1%}")

    # A rank correlation over a variable that is zero for almost every
    # observation is not a test of anything: the ranks among the zeros are all
    # tied, so the statistic is decided by a handful of points. Say so rather
    # than let a p-value below .05 look like a result.
    print()
    for horizon in HORIZONS:
        pairs = [(o.attention, o.returns[horizon]) for o in observations
                 if o.returns[horizon] is not None]
        nonzero = sum(1 for a, _ in pairs if a > 0)
        share = nonzero / len(pairs) if pairs else 0
        verdict = (
            "UNDERPOWERED -- attention is zero for "
            f"{100 * (1 - share):.0f}% of the sample; any rho is decided by "
            f"{nonzero} observations"
            if nonzero < 20 else "sample adequate"
        )
        print(f"  {horizon}d: {verdict}")

    print("\ndescriptive, independent of attention:")
    for horizon in HORIZONS:
        rets = sorted(o.returns[horizon] for o in observations
                      if o.returns[horizon] is not None)
        if not rets:
            continue
        n = len(rets)
        neg = sum(1 for r in rets if r < 0) / n
        print(f"  {horizon}d  n={n:>4}  median {rets[n//2]:>7.1%}  "
              f"mean {sum(rets)/n:>7.1%}  negative {neg:.0%}")

    print("\nmost-discussed listings in window:")
    for o in sorted(observations, key=lambda o: -o.attention)[:8]:
        r30 = o.returns[30]
        print(f"   {o.attention:>4} mentions  {o.ticker:<7} {o.listed_on}  "
              f"open ${o.open_price:<8.2f} 30d {'n/a' if r30 is None else f'{r30:+.1%}'}"
              f"  {o.name[:30]}")


if __name__ == "__main__":
    report(asyncio.run(collect()))
