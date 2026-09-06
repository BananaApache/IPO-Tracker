"""Scores backend.match.matcher against the hand-labelled set.

    uv run python -m tests.evaluate_matching
"""

import asyncio
import json
import pathlib
import sys

import asyncpg

from backend.config import get_settings
from backend.match.matcher import ACCEPT_THRESHOLD, AliasRow, match
from tests.fixtures.matching_labels import NAIVE_SUBSTRING_PRECISION, TRUE_MATCHES

SC = pathlib.Path(
    "/private/tmp/claude-501/-Users-daniel-Documents-coding-stuff-IPOTracker/"
    "1fbd3569-2463-4bbf-a251-86351352c9e6/scratchpad/hn"
)


async def _aliases() -> tuple[list[AliasRow], dict[int, str]]:
    connection = await asyncpg.connect(get_settings().database_dsn, statement_cache_size=0)
    rows = await connection.fetch(
        "SELECT a.id, a.issuer_id, a.normalized_alias, a.kind, i.cik, i.legal_name "
        "FROM aliases a JOIN issuers i ON i.id = a.issuer_id"
    )
    await connection.close()
    return (
        [AliasRow(r["id"], r["issuer_id"], r["normalized_alias"], r["kind"]) for r in rows],
        {r["issuer_id"]: r["cik"] for r in rows},
    )


def _labelled_items() -> list[dict]:
    sample = json.loads((SC / "sample.json").read_text())
    rest = json.loads((SC / "rest.json").read_text())
    seen, out = set(), []
    for item in [*sample, *rest]:
        if item["source_uid"] in seen:
            continue
        seen.add(item["source_uid"])
        out.append(item)
    return out


def main(verbose: bool = True) -> None:
    aliases, cik_by_issuer = asyncio.run(_aliases())
    items = _labelled_items()

    tp = fp = fn = 0
    review_tp = review_fp = 0
    misses, spurious = [], []

    for item in items:
        uid = item["source_uid"]
        truth = TRUE_MATCHES.get(uid)
        result = match(aliases, item.get("title") or "", item.get("body_excerpt") or "")

        accepted = result is not None and result.confidence >= ACCEPT_THRESHOLD
        predicted_cik = cik_by_issuer.get(result.issuer_id) if result else None

        if truth and accepted and predicted_cik == truth[0]:
            tp += 1
        elif truth and not accepted:
            fn += 1
            misses.append((uid, result, item.get("title", "")[:52]))
        elif not truth and accepted:
            fp += 1
            spurious.append((uid, result, item.get("title", "")[:52]))
        elif truth and accepted:
            fp += 1
            fn += 1

        if result is not None and result.needs_review:
            if truth:
                review_tp += 1
            else:
                review_fp += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    if verbose:
        if misses:
            print("MISSED (labelled true, not accepted):")
            for uid, r, title in misses:
                c = f"{r.confidence:.2f} {','.join(r.reasons)}" if r else "no candidate"
                print(f"   [{uid}] {c}\n              {title}")
        if spurious:
            print(f"\nSPURIOUS (accepted, labelled false) -- {len(spurious)}:")
            for uid, r, title in spurious[:12]:
                print(f"   [{uid}] {r.confidence:.2f} {','.join(r.reasons)[:44]}  {title}")
            if len(spurious) > 12:
                print(f"   ... and {len(spurious) - 12} more")

    print("\n" + "=" * 62)
    print(f"labelled items       : {len(items)}   true positives: {len(TRUE_MATCHES)}")
    print(f"ACCEPTED  tp={tp} fp={fp} fn={fn}")
    print(f"          precision {precision:.3f}   recall {recall:.3f}   F1 {f1:.3f}")
    print(f"REVIEW QUEUE  {review_tp} true / {review_fp} false  ({review_tp + review_fp} rows)")
    print(f"baseline (naive substring) precision {NAIVE_SUBSTRING_PRECISION:.3f}")
    print("=" * 62)


if __name__ == "__main__":
    main(verbose="-q" not in sys.argv)
